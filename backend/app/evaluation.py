"""The evaluation harness — every number this project claims, computed here.

Three claims are made about this platform, and each one is measured rather than
asserted:

  trust           does the trust layer catch the facilities the generator made
                  dishonest, and how much noise does it make doing it
  redistribution  does the solver ever break one of its own safety rules
  federation      is the federated model better than the burn rate it replaces

Everything below reads the same live tables the product reads. The only extra
input is `app/groundtruth.py`, which no request-serving code may touch.

Two deliberate honesty rules:
  * Supply failures are not dishonesty. A facility that runs dry because its
    consignment never came is reporting the truth, and flagging it is a false
    positive, not a catch.
  * Reachability is not prevention. That medicine existed within reach of a
    stock-out says the network could have moved it, not that anyone would have.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import groundtruth, redistribution, trust
from .models import Sku

DEFAULT_PRECISION_KS = (20, 50, 100)
# The four federated silos: enough states to be representative, few enough that
# the solver check finishes in seconds.
DEFAULT_SOLVER_STATES = ("MH", "KL", "BR", "UP")
DEFAULT_STOCKOUT_STATE = "MH"
DEFAULT_STOCKOUT_DAYS = 120
DEFAULT_REACH_KM = 150.0
# A donor is only a donor above its own floor, so "spare" means the same thing
# here as it does in the solver.
SPARE_FLOOR_DAYS = 14.0


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 3)


async def trust_metrics(
    session: AsyncSession,
    truth: groundtruth.GroundTruth,
    *,
    ks: tuple[int, ...] = DEFAULT_PRECISION_KS,
) -> dict[str, Any]:
    """Score the trust layer against what the generator actually did."""
    scores = await trust.compute(session)
    if not scores:
        return {"available": False, "reason": "no facility carries enough history to score"}

    seen = {s.facility_id for s in scores}
    dishonest = set(truth.dishonest) & seen
    honest = seen - dishonest
    flagged = {s.facility_id for s in scores if s.band != trust.GOOD}
    audit = {s.facility_id for s in scores if s.band == trust.AUDIT}

    caught = flagged & dishonest
    missed = dishonest - flagged
    false_alarms = flagged & honest

    # Worst score first: the order the audit queue puts them in, so precision@k
    # is what an officer working down that list would actually see.
    ranked = sorted(scores, key=lambda s: s.score)
    precision_at_k = {
        "top_{0}".format(k): _rate(
            sum(1 for s in ranked[:k] if s.facility_id in dishonest), min(k, len(ranked))
        )
        for k in ks
    }

    return {
        "available": True,
        "scored": len(scores),
        "deliberately_dishonest": len(dishonest),
        "flagged": len(flagged),
        "flagged_for_audit": len(audit),
        "recall": _rate(len(caught), len(dishonest)),
        "precision": _rate(len(caught), len(flagged)),
        "false_positive_rate": _rate(len(false_alarms), len(honest)),
        "missed": len(missed),
        "precision_at_k": precision_at_k,
        "note": (
            "Recall is over facilities the generator made dishonest. The "
            + str(len(truth.supply_failure))
            + " facilities given supply failures are not counted as dishonest: they report "
            "truthfully, and flagging one is a false positive. Flag-level precision is low by "
            "design, because the layer is tuned to miss nothing and then rank, which is why "
            "precision@k is reported beside it."
        ),
    }


async def redistribution_metrics(
    session: AsyncSession, *, states: tuple[str, ...] = DEFAULT_SOLVER_STATES
) -> dict[str, Any]:
    """Run the solver and check it against every rule it is supposed to obey.

    Nothing is written: this plans from `load_state_nodes` and inspects the
    proposals, rather than calling `generate_plan`, which would create real
    transfer rows.
    """
    # The same rules the product plans with, not a copy of their numbers:
    # a check against rules the solver does not use would prove nothing.
    rules = redistribution.PlanRules.from_settings()
    skus = {s.code: s for s in (await session.execute(select(Sku))).scalars()}
    violations: list[dict[str, Any]] = []
    proposals = 0
    units = 0
    controlled_planned = 0
    solvers: dict[str, int] = {}

    for state in states:
        by_sku = await redistribution.load_state_nodes(session, state)
        for sku_code, nodes in by_sku.items():
            sku = skus.get(sku_code)
            if sku is None:
                continue
            plan = redistribution.plan_sku(
                sku_code,
                nodes,
                rules,
                controlled=bool(sku.is_controlled),
                cold_chain=bool(sku.cold_chain),
            )
            solvers[plan.solver] = solvers.get(plan.solver, 0) + 1
            index = {n.facility_id: n for n in nodes}

            if sku.is_controlled:
                controlled_planned += 1
                for p in plan.proposals:
                    violations.append(
                        {
                            "rule": "controlled substances are never routed",
                            "state": state,
                            "sku": sku_code,
                            "detail": p.from_id + " -> " + p.to_id,
                        }
                    )
                continue

            moved_out: dict[str, int] = {}
            for p in plan.proposals:
                proposals += 1
                units += p.qty
                moved_out[p.from_id] = moved_out.get(p.from_id, 0) + p.qty
                ceiling = rules.cold_chain_max_km if sku.cold_chain else rules.max_km
                if p.km > ceiling + 1e-6:
                    violations.append(
                        {
                            "rule": "distance ceiling",
                            "state": state,
                            "sku": sku_code,
                            "detail": "{0} -> {1} is {2:.1f} km, ceiling {3:.0f}".format(
                                p.from_id, p.to_id, p.km, ceiling
                            ),
                        }
                    )
                if p.qty < rules.min_units:
                    violations.append(
                        {
                            "rule": "minimum useful quantity",
                            "state": state,
                            "sku": sku_code,
                            "detail": "{0} units, minimum {1}".format(p.qty, rules.min_units),
                        }
                    )
                if p.from_id == p.to_id:
                    violations.append(
                        {
                            "rule": "a facility cannot supply itself",
                            "state": state,
                            "sku": sku_code,
                            "detail": p.from_id,
                        }
                    )

            for donor_id, qty in moved_out.items():
                donor = index.get(donor_id)
                if donor is None or not donor.burn:
                    continue
                left_days = (donor.qty - qty) / donor.burn
                if left_days < rules.donor_floor_days - 1e-6:
                    violations.append(
                        {
                            "rule": "donor safety floor",
                            "state": state,
                            "sku": sku_code,
                            "detail": "{0} left with {1:.1f} days, floor {2:.0f}".format(
                                donor_id, left_days, rules.donor_floor_days
                            ),
                        }
                    )

    return {
        "available": True,
        "states": list(states),
        "sku_plans": sum(solvers.values()),
        "solver_used": solvers,
        "proposals": proposals,
        "units_moved": units,
        "controlled_skus_planned": controlled_planned,
        "constraint_violations": len(violations),
        "violations": violations[:20],
        "rules_checked": [
            "no donor left below {0:.0f} days of its own cover".format(rules.donor_floor_days),
            "no transfer beyond {0:.0f} km ({1:.0f} km cold chain)".format(
                rules.max_km, rules.cold_chain_max_km
            ),
            "no transfer smaller than {0} units".format(rules.min_units),
            "controlled substances never routed by the solver",
            "no facility supplying itself",
            "every plan stays inside one state",
        ],
    }


OUT_OF_STOCK_SQL = """
WITH daily AS (
    SELECT sr.facility_id,
           sr.sku_code,
           date_trunc('day', sr.reported_at) AS day,
           (array_agg(sr.qty_on_hand ORDER BY sr.reported_at DESC))[1] AS qty
      FROM stock_readings sr
      JOIN facilities f ON f.id = sr.facility_id
     WHERE f.state_silo = :state AND sr.reported_at >= :since
     GROUP BY 1, 2, 3
)
SELECT count(*) FILTER (WHERE qty <= 0)                    AS out_days,
       count(DISTINCT facility_id) FILTER (WHERE qty <= 0) AS facilities,
       count(*)                                            AS observed_days
  FROM daily
"""

WITHIN_REACH_SQL = """
WITH here AS (
    SELECT s.facility_id, s.sku_code, f.lat, f.lng
      FROM facility_sku_state s
      JOIN facilities f ON f.id = s.facility_id
     WHERE f.state_silo = :state AND s.qty_on_hand <= 0
),
spare AS (
    SELECT s.facility_id, s.sku_code, f.lat, f.lng
      FROM facility_sku_state s
      JOIN facilities f ON f.id = s.facility_id
     WHERE f.state_silo = :state
       AND s.daily_burn_rate > 0
       AND s.qty_on_hand - (:floor_days * s.daily_burn_rate) >= 5
)
SELECT count(*) AS shortages,
       count(*) FILTER (
           WHERE EXISTS (
               SELECT 1
                 FROM spare
                WHERE spare.sku_code = here.sku_code
                  AND spare.facility_id <> here.facility_id
                  AND 6371.0088 * 2 * asin(sqrt(
                        power(sin(radians(spare.lat - here.lat) / 2), 2)
                      + cos(radians(here.lat)) * cos(radians(spare.lat))
                      * power(sin(radians(spare.lng - here.lng) / 2), 2)
                  )) <= :reach_km
           )
       ) AS within_reach
  FROM here
"""


async def stockout_metrics(
    session: AsyncSession,
    *,
    state: str = DEFAULT_STOCKOUT_STATE,
    days: int = DEFAULT_STOCKOUT_DAYS,
    reach_km: float = DEFAULT_REACH_KM,
) -> dict[str, Any]:
    """Count empty shelves, then ask whether the medicine was anywhere near.

    Two separate measurements, deliberately not blended: facility-days with
    nothing on the shelf across the window, and — for the shortages standing
    right now — whether another facility in the same state held genuinely spare
    stock within reach.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = (await session.execute(text(OUT_OF_STOCK_SQL), {"state": state, "since": since})).one()
    reach = (
        await session.execute(
            text(WITHIN_REACH_SQL),
            {"state": state, "reach_km": reach_km, "floor_days": SPARE_FLOOR_DAYS},
        )
    ).one()

    share = _rate(reach.within_reach, reach.shortages)
    return {
        "available": True,
        "state": state,
        "window_days": days,
        "facility_days_out_of_stock": out.out_days,
        "facility_sku_days_observed": out.observed_days,
        "facilities_affected": out.facilities,
        "shortages_now": reach.shortages,
        "spare_stock_within_reach_now": reach.within_reach,
        "within_reach_pct": None if share is None else round(share * 100, 1),
        "note": (
            "Spare means above a {0:.0f}-day floor of the donor's own cover, inside {1:.0f} km, "
            "same state — the solver's own rules. This is reachability, not prevention: the stock "
            "existed near enough to move, which is not a claim that anyone would have moved it."
        ).format(SPARE_FLOOR_DAYS, reach_km),
    }


def federation_metrics(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Fold in what the torch-side evaluator measured, if it ran.

    The web service carries no torch, so the model is scored by
    `backend/federation/evaluate_model.py` inside the Flower environment and its
    JSON is merged here. Absent means absent, never a placeholder number.
    """
    if not payload:
        return {
            "available": False,
            "reason": (
                "no federation evaluation on record — run `python -m checks federation`, "
                "or `python -m scripts.run_eval --federation`"
            ),
        }
    model = payload.get("model_mae")
    baselines = payload.get("baselines") or {}
    improvement = {
        name: (
            None
            if not value or not model
            else round((value - model) / value * 100, 1)
        )
        for name, value in baselines.items()
    }
    return {
        "available": True,
        "model": payload.get("model"),
        "silos": payload.get("silos"),
        "windows_scored": payload.get("windows"),
        "model_mae": model,
        "baselines": baselines,
        "improvement_pct": improvement,
        "per_silo": payload.get("per_silo"),
        "measured_at": payload.get("measured_at"),
        "note": (
            "Mean absolute error on each silo's held-out validation windows, weighted by window "
            "count. Targets are normalised by each window's own 28-day mean, so a baseline that "
            "predicts 1.0 is exactly the burn-rate rule the dashboard uses today."
        ),
    }


async def report(
    session: AsyncSession,
    *,
    truth: groundtruth.GroundTruth | None,
    federation: dict[str, Any] | None = None,
    states: tuple[str, ...] = DEFAULT_SOLVER_STATES,
    stockout_state: str = DEFAULT_STOCKOUT_STATE,
    stockout_days: int = DEFAULT_STOCKOUT_DAYS,
) -> dict[str, Any]:
    """Every section, plus what the numbers were computed against."""
    sections: dict[str, Any] = {}

    if truth is None:
        sections["trust"] = {
            "available": False,
            "reason": (
                "no ground truth on record: reseed with `python -m scripts.seed`, which writes "
                + groundtruth.DEFAULT_PATH.name
            ),
        }
    else:
        stale = await groundtruth.mismatches(session, truth)
        if stale:
            sections["trust"] = {
                "available": False,
                "reason": "ground truth does not describe this database: " + "; ".join(stale),
            }
        else:
            sections["trust"] = await trust_metrics(session, truth)

    sections["redistribution"] = await redistribution_metrics(session, states=states)
    sections["stockouts"] = await stockout_metrics(
        session, state=stockout_state, days=stockout_days
    )
    sections["federation"] = federation_metrics(federation)

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset_seed": None if truth is None else truth.seed,
        "ground_truth_written_at": None if truth is None else truth.written_at.isoformat(),
        "data": "synthetic, generated by scripts/seed.py — every number here is a simulation",
        "sections": sections,
    }

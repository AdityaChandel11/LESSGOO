"""Redistribution engine — spec 12.3.

Finds facilities running short of a medicine, matches them with facilities in
the same state that can spare it, and proposes transfers for an officer to
approve. Nothing moves until a human approves (spec rule 6).

The solver half of this module is pure — plain data in, proposals out — so the
safety rules are unit-testable without a database:

  * a donor is never taken below `donor_floor_days` of its own cover,
  * a controlled substance is never proposed; it goes to a manual queue,
  * cold-chain items only travel within `cold_chain_max_km`,
  * nothing travels further than `max_transfer_km`.

Why same-state only: cross-state transfers need state-level sign-off in the
real system, so the automated plan stays inside one state's authority. Needs
it cannot meet are reported, not silently dropped — they are the signal to
escalate to the state warehouse.

Distances are straight-line x road factor until the Routes API matrix is
cached (MAPS_MODE=google); every transfer records which one it used.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from . import maps, movements
from .config import settings
from .models import (
    Approval,
    Facility,
    FacilitySkuState,
    Sku,
    StockReading,
    Transfer,
)

SolverName = Literal["ortools", "greedy", "manual"]
EARTH_RADIUS_KM = 6371.0088


# ============================================================ pure solver ===


@dataclass(frozen=True)
class StockNode:
    facility_id: str
    name: str
    district: str
    lat: float
    lng: float
    qty: float
    burn: float
    days: float | None
    status: str


@dataclass(frozen=True)
class PlanRules:
    trigger_days: float
    target_days: float
    donor_floor_days: float
    road_factor: float
    max_km: float
    cold_chain_max_km: float
    avg_speed_kmh: float
    handling_hours: float
    min_units: int
    critical_cost_weight: float

    @classmethod
    def from_settings(cls) -> "PlanRules":
        return cls(
            trigger_days=settings.at_risk_days,
            target_days=settings.recipient_target_days,
            donor_floor_days=settings.donor_floor_days,
            road_factor=settings.road_factor,
            max_km=settings.max_transfer_km,
            cold_chain_max_km=settings.cold_chain_max_km,
            avg_speed_kmh=settings.avg_speed_kmh,
            handling_hours=settings.handling_hours,
            min_units=settings.min_transfer_units,
            critical_cost_weight=settings.critical_cost_weight,
        )


@dataclass
class Proposal:
    from_id: str
    to_id: str
    sku_code: str
    qty: int
    km: float
    eta_hours: float
    rationale: dict


@dataclass
class Shortfall:
    facility_id: str
    name: str
    district: str
    days: float | None
    units_needed: int
    reason: str


@dataclass
class SkuPlan:
    sku_code: str
    solver: SolverName
    proposals: list[Proposal] = field(default_factory=list)
    unmet: list[Shortfall] = field(default_factory=list)


def road_km(donors: list[StockNode], recipients: list[StockNode], road_factor: float) -> np.ndarray:
    """Donor x recipient matrix of estimated road km (haversine x road factor)."""
    if not donors or not recipients:
        return np.zeros((len(donors), len(recipients)))
    d_lat = np.radians([n.lat for n in donors])[:, None]
    d_lng = np.radians([n.lng for n in donors])[:, None]
    r_lat = np.radians([n.lat for n in recipients])[None, :]
    r_lng = np.radians([n.lng for n in recipients])[None, :]
    a = (
        np.sin((r_lat - d_lat) / 2) ** 2
        + np.cos(d_lat) * np.cos(r_lat) * np.sin((r_lng - d_lng) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a)) * road_factor


def split_roles(
    nodes: list[StockNode], rules: PlanRules
) -> tuple[list[tuple[StockNode, int]], list[tuple[StockNode, int]]]:
    """(donors with spare units, recipients with units needed).

    Facilities with no measured usage are neither: without a burn rate there is
    no way to know what they can safely give or how much they need.
    """
    donors: list[tuple[StockNode, int]] = []
    recipients: list[tuple[StockNode, int]] = []
    for n in nodes:
        if n.burn <= 0:
            continue
        days = n.qty / n.burn
        if days < rules.trigger_days:
            need = math.ceil(rules.target_days * n.burn - n.qty)
            if need >= rules.min_units:
                recipients.append((n, need))
        else:
            spare = math.floor(n.qty - rules.donor_floor_days * n.burn)
            if spare >= rules.min_units:
                donors.append((n, spare))
    return donors, recipients


def _eta(km: float, rules: PlanRules) -> float:
    return round(km / rules.avg_speed_kmh + rules.handling_hours, 2)


def _solve_ortools(
    donors: list[tuple[StockNode, int]],
    recipients: list[tuple[StockNode, int]],
    km: np.ndarray,
    limit_km: float,
    rules: PlanRules,
) -> list[tuple[int, int, int]] | None:
    """Max flow at minimum cost. Returns (donor_idx, recipient_idx, qty) or None
    if OR-Tools is unavailable or does not reach an optimal solution."""
    try:
        from ortools.graph.python import min_cost_flow
    except ImportError:
        return None

    smcf = min_cost_flow.SimpleMinCostFlow()
    n_d = len(donors)
    arcs: list[tuple[int, int, int]] = []
    for i, (_, spare) in enumerate(donors):
        for j, (r, need) in enumerate(recipients):
            dist = float(km[i, j])
            if dist > limit_km:
                continue
            cap = min(spare, need)
            if cap < rules.min_units:
                continue
            weight = rules.critical_cost_weight if r.status == "critical" else 1.0
            cost = max(1, int(round(dist * 10 * weight)))
            arc = smcf.add_arc_with_capacity_and_unit_cost(i, n_d + j, cap, cost)
            arcs.append((arc, i, j))
    if not arcs:
        return []

    for i, (_, spare) in enumerate(donors):
        smcf.set_node_supply(i, spare)
    for j, (_, need) in enumerate(recipients):
        smcf.set_node_supply(n_d + j, -need)

    # Supply and demand are almost never equal, so plain solve() would refuse.
    # This variant maximises total flow first, then minimises cost — which,
    # with cheaper arcs into critical facilities, sends scarce stock there first.
    if smcf.solve_max_flow_with_min_cost() != smcf.OPTIMAL:
        return None
    return [(i, j, smcf.flow(arc)) for arc, i, j in arcs if smcf.flow(arc) > 0]


def _solve_greedy(
    donors: list[tuple[StockNode, int]],
    recipients: list[tuple[StockNode, int]],
    km: np.ndarray,
    limit_km: float,
    rules: PlanRules,
) -> list[tuple[int, int, int]]:
    """Zero-dependency fallback: most urgent recipient first, nearest donor first."""
    spare = [s for _, s in donors]
    flows: list[tuple[int, int, int]] = []
    order = sorted(
        range(len(recipients)),
        key=lambda j: (recipients[j][0].status != "critical", recipients[j][0].qty / recipients[j][0].burn),
    )
    for j in order:
        need = recipients[j][1]
        for i in sorted(range(len(donors)), key=lambda i: km[i, j]):
            if need < rules.min_units:
                break
            if km[i, j] > limit_km or spare[i] < rules.min_units:
                continue
            qty = min(spare[i], need)
            if qty < rules.min_units:
                continue
            flows.append((i, j, qty))
            spare[i] -= qty
            need -= qty
    return flows


def plan_sku(
    sku_code: str,
    nodes: list[StockNode],
    rules: PlanRules,
    *,
    controlled: bool = False,
    cold_chain: bool = False,
    solver: Literal["auto", "ortools", "greedy"] = "auto",
) -> SkuPlan:
    donors, recipients = split_roles(nodes, rules)

    if controlled:
        return SkuPlan(
            sku_code=sku_code,
            solver="manual",
            unmet=[
                Shortfall(
                    r.facility_id, r.name, r.district, r.qty / r.burn, need,
                    "Controlled substance: never routed automatically. Needs manual review.",
                )
                for r, need in recipients
            ],
        )

    limit_km = rules.cold_chain_max_km if cold_chain else rules.max_km
    km = road_km([d for d, _ in donors], [r for r, _ in recipients], rules.road_factor)

    flows: list[tuple[int, int, int]] | None = None
    used: SolverName = "greedy"
    if solver in ("auto", "ortools"):
        flows = _solve_ortools(donors, recipients, km, limit_km, rules)
        used = "ortools"
    if flows is None:
        flows = _solve_greedy(donors, recipients, km, limit_km, rules)
        used = "greedy"

    # A max-flow solution can split a need into slivers; a trip for three
    # tablets helps nobody, so drop anything under the minimum.
    flows = [f for f in flows if f[2] >= rules.min_units]

    received = [0] * len(recipients)
    given = [0] * len(donors)
    plan = SkuPlan(sku_code=sku_code, solver=used)

    for i, j, qty in flows:
        d, spare = donors[i]
        r, need = recipients[j]
        dist = float(km[i, j])
        given[i] += qty
        received[j] += qty
        plan.proposals.append(
            Proposal(
                from_id=d.facility_id,
                to_id=r.facility_id,
                sku_code=sku_code,
                qty=int(qty),
                km=round(dist, 1),
                eta_hours=_eta(dist, rules),
                rationale={},
            )
        )

    # Each transfer reports two outcomes, because officers approve one at a
    # time: what *this* transfer achieves alone, and what the whole plan
    # achieves once every transfer touching the same facility is approved.
    donor_idx = {d.facility_id: i for i, (d, _) in enumerate(donors)}
    recip_idx = {r.facility_id: j for j, (r, _) in enumerate(recipients)}
    incoming = [0] * len(recipients)
    outgoing = [0] * len(donors)
    for p in plan.proposals:
        incoming[recip_idx[p.to_id]] += 1
        outgoing[donor_idx[p.from_id]] += 1

    for p in plan.proposals:
        i, j = donor_idx[p.from_id], recip_idx[p.to_id]
        d, spare = donors[i]
        r, need = recipients[j]
        if p.qty >= need:
            binding = "covers the full need"
        elif p.qty >= spare:
            binding = "all the stock this donor can spare"
        else:
            binding = "part of the need, shared across donors"
        p.rationale = {
            "solver": used,
            "binding": binding,
            "recipient_status": r.status,
            "recipient_days_before": round(r.qty / r.burn, 2),
            "recipient_days_after_this": round((r.qty + p.qty) / r.burn, 2),
            "recipient_days_after_plan": round((r.qty + received[j]) / r.burn, 2),
            "recipient_incoming_transfers": incoming[j],
            "recipient_target_days": rules.target_days,
            "donor_days_before": round(d.qty / d.burn, 2),
            "donor_days_after_this": round((d.qty - p.qty) / d.burn, 2),
            "donor_days_after_plan": round((d.qty - given[i]) / d.burn, 2),
            "donor_outgoing_transfers": outgoing[i],
            "donor_floor_days": rules.donor_floor_days,
            "distance_limit_km": limit_km,
            "cold_chain": cold_chain,
            "distance_basis": f"straight line x {rules.road_factor} road factor",
        }

    for j, (r, need) in enumerate(recipients):
        short = need - received[j]
        if short >= rules.min_units:
            plan.unmet.append(
                Shortfall(
                    r.facility_id, r.name, r.district, r.qty / r.burn, short,
                    f"No facility within {limit_km:.0f} km has {sku_code} to spare. Escalate to the state warehouse.",
                )
            )

    return plan


# =============================================================== database ===


async def load_state_nodes(
    session: AsyncSession, state: str, sku: str | None = None
) -> dict[str, list[StockNode]]:
    sql = text(f"""
        SELECT s.sku_code, f.id, f.name, f.district, f.lat, f.lng,
               s.qty_on_hand, s.daily_burn_rate, s.days_of_stock, s.status
        FROM facility_sku_state s
        JOIN facilities f ON f.id = s.facility_id
        WHERE f.state_silo = :state {"AND s.sku_code = :sku" if sku else ""}
    """)
    params: dict[str, object] = {"state": state}
    if sku:
        params["sku"] = sku
    out: dict[str, list[StockNode]] = {}
    for row in (await session.execute(sql, params)).all():
        out.setdefault(row[0], []).append(
            StockNode(
                facility_id=row[1], name=row[2], district=row[3],
                lat=row[4], lng=row[5], qty=float(row[6]),
                burn=float(row[7] or 0), days=row[8], status=row[9],
            )
        )
    return out


def _refresh_plan_outcomes(
    proposals: list[Proposal], nodes: dict[tuple[str, str], StockNode]
) -> None:
    """Recompute each transfer's whole-plan outcome from the transfers that
    survived. If a sibling transfer was withdrawn (its road was too long), the
    remaining cards must not still promise what the withdrawn one delivered."""
    incoming: dict[tuple[str, str], list[Proposal]] = defaultdict(list)
    outgoing: dict[tuple[str, str], list[Proposal]] = defaultdict(list)
    for p in proposals:
        incoming[(p.to_id, p.sku_code)].append(p)
        outgoing[(p.from_id, p.sku_code)].append(p)

    for p in proposals:
        r = nodes[(p.to_id, p.sku_code)]
        d = nodes[(p.from_id, p.sku_code)]
        ins = incoming[(p.to_id, p.sku_code)]
        outs = outgoing[(p.from_id, p.sku_code)]
        p.rationale["recipient_incoming_transfers"] = len(ins)
        p.rationale["recipient_days_after_plan"] = round((r.qty + sum(x.qty for x in ins)) / r.burn, 2)
        p.rationale["donor_outgoing_transfers"] = len(outs)
        p.rationale["donor_days_after_plan"] = round((d.qty - sum(x.qty for x in outs)) / d.burn, 2)


@dataclass
class PlanResult:
    state: str
    sku: str | None
    solver: str
    generated_at: datetime
    transfer_ids: list[int]
    unmet: list[dict]
    manual_review: list[dict]


async def generate_plan(
    session: AsyncSession, state: str, sku: str | None = None
) -> PlanResult:
    """Replace this state's open proposals with a freshly solved plan.

    Decided transfers (approved or rejected) are history and are never touched.
    """
    rules = PlanRules.from_settings()
    skus = {s.code: s for s in (await session.execute(select(Sku))).scalars().all()}
    nodes_by_sku = await load_state_nodes(session, state, sku)

    in_state = select(Facility.id).where(Facility.state_silo == state)
    stmt = delete(Transfer).where(
        Transfer.status == "proposed", Transfer.to_facility.in_(in_state)
    )
    if sku:
        stmt = stmt.where(Transfer.sku_code == sku)
    await session.execute(stmt)

    unmet: list[dict] = []
    manual: list[dict] = []
    solvers: set[str] = set()
    plans: list[tuple[Sku, SkuPlan]] = []

    for code, nodes in sorted(nodes_by_sku.items()):
        meta = skus.get(code)
        if meta is None:
            continue
        plan = plan_sku(
            code, nodes, rules,
            controlled=meta.is_controlled, cold_chain=meta.cold_chain,
        )
        if plan.solver != "manual":
            solvers.add(plan.solver)
        plans.append((meta, plan))
        bucket = manual if plan.solver == "manual" else unmet
        bucket.extend(
            {
                "facility_id": s.facility_id,
                "name": s.name,
                "district": s.district,
                "sku_code": code,
                "sku_name": meta.name,
                "days": round(s.days, 2) if s.days is not None else None,
                "units_needed": s.units_needed,
                "reason": s.reason,
            }
            for s in plan.unmet
        )

    # ---- road distances, only for what the solver actually proposed ----
    nodes_by_key = {(n.facility_id, code): n for code, ns in nodes_by_sku.items() for n in ns}
    points = {
        n.facility_id: maps.Point(n.facility_id, n.lat, n.lng)
        for ns in nodes_by_sku.values()
        for n in ns
    }
    pairs = {(p.from_id, p.to_id) for _, plan in plans for p in plan.proposals}
    legs = await maps.road_legs(session, pairs, points)

    kept: list[tuple[Sku, Proposal]] = []
    for meta, plan in plans:
        limit = rules.cold_chain_max_km if meta.cold_chain else rules.max_km
        for p in plan.proposals:
            leg = legs[(p.from_id, p.to_id)]
            p.km = leg.km
            p.eta_hours = round(leg.minutes / 60 + rules.handling_hours, 2)
            p.rationale["distance_source"] = leg.source
            p.rationale["distance_basis"] = (
                "road distance (Google Routes)"
                if leg.source == maps.SOURCE_GOOGLE
                else f"straight line x {rules.road_factor} road factor"
            )
            if leg.source == maps.SOURCE_GOOGLE and leg.km > limit:
                # The straight-line estimate said this was in range; the actual
                # road says otherwise, so the transfer is withdrawn, not kept.
                r = nodes_by_key[(p.to_id, p.sku_code)]
                unmet.append(
                    {
                        "facility_id": r.facility_id,
                        "name": r.name,
                        "district": r.district,
                        "sku_code": p.sku_code,
                        "sku_name": meta.name,
                        "days": round(r.qty / r.burn, 2) if r.burn else None,
                        "units_needed": p.qty,
                        "reason": (
                            f"The nearest donor with spare stock is {leg.km:.0f} km away by road, "
                            f"beyond the {limit:.0f} km limit. Escalate to the state warehouse."
                        ),
                    }
                )
                continue
            kept.append((meta, p))

    _refresh_plan_outcomes([p for _, p in kept], nodes_by_key)

    rows = [
        Transfer(
            from_facility=p.from_id,
            to_facility=p.to_id,
            sku_code=p.sku_code,
            qty=p.qty,
            route_km=p.km,
            eta_hours=p.eta_hours,
            route_source=p.rationale["distance_source"],
            status="proposed",
            triggered_by="threshold",
            rationale=p.rationale,
        )
        for _, p in kept
    ]
    session.add_all(rows)
    await session.commit()

    unmet.sort(key=lambda u: (u["days"] if u["days"] is not None else 1e9))
    return PlanResult(
        state=state,
        sku=sku,
        solver="+".join(sorted(solvers)) or "none",
        generated_at=datetime.now(timezone.utc),
        transfer_ids=[r.id for r in rows],
        unmet=unmet,
        manual_review=manual,
    )


async def list_transfers(
    session: AsyncSession,
    *,
    state: str | None = None,
    statuses: list[str] | None = None,
    ids: list[int] | None = None,
    limit: int = 500,
) -> list[dict]:
    src = aliased(Facility)
    dst = aliased(Facility)
    stmt = (
        select(Transfer, src, dst, Sku)
        .join(src, src.id == Transfer.from_facility)
        .join(dst, dst.id == Transfer.to_facility)
        .join(Sku, Sku.code == Transfer.sku_code)
    )
    if state:
        stmt = stmt.where(dst.state_silo == state)
    if statuses:
        stmt = stmt.where(Transfer.status.in_(statuses))
    if ids is not None:
        stmt = stmt.where(Transfer.id.in_(ids))
        # An explicit id list is fetched whole; the page limit is for browsing.
        limit = max(limit, len(ids))
    stmt = stmt.order_by(Transfer.created_at.desc()).limit(limit)

    out = []
    for t, f, d, s in (await session.execute(stmt)).all():
        out.append(
            {
                "id": t.id,
                "status": t.status,
                "sku_code": s.code,
                "sku_name": s.name,
                "unit": s.unit,
                "cold_chain": s.cold_chain,
                "qty": float(t.qty or 0),
                "route_km": float(t.route_km or 0),
                "eta_hours": float(t.eta_hours or 0),
                "route_source": t.route_source,
                "triggered_by": t.triggered_by,
                "created_at": t.created_at,
                "rationale": t.rationale or {},
                "from": {"id": f.id, "name": f.name, "district": f.district, "lat": f.lat, "lng": f.lng},
                "to": {"id": d.id, "name": d.name, "district": d.district, "lat": d.lat, "lng": d.lng},
            }
        )
    # Most urgent recipients first, then shortest trips.
    out.sort(
        key=lambda x: (
            x["rationale"].get("recipient_days_before", 1e9),
            x["route_km"],
        )
    )
    return out


def _days(value) -> str:
    """Days as the trip card writes them: under one is "less than 1 day", never 0."""
    value = float(value)
    if value < 1:
        return "less than 1 day"
    return "{0:g} days".format(round(value, 1))


def why_rows(items: list[dict], critical_days: float) -> list[str]:
    """One trip's figures, exactly as its card shows them, for the explanation.

    `items` are rows from `list_transfers` sharing one donor and one receiver.
    Nothing is read that the card does not already display.
    """
    first = items[0]
    rows = [
        f"Donor: {first['from']['name']}, {first['from']['district']} district",
        f"Receiver: {first['to']['name']}, {first['to']['district']} district",
        f"Road distance: about {round(first['route_km'])} km",
        f"Under {critical_days:g} days of stock counts as critical.",
    ]
    for t in items:
        r = t["rationale"]
        parts = [f"{t['sku_name']}: send {round(t['qty'])} {t['unit']}."]
        if r.get("recipient_days_before") is not None:
            parts.append(f"Receiver has {_days(r['recipient_days_before'])} of stock now")
            if r.get("recipient_days_after_this") is not None:
                parts[-1] += f", {_days(r['recipient_days_after_this'])} after this transfer."
            else:
                parts[-1] += "."
        if r.get("donor_days_after_plan") is not None:
            parts.append(f"Donor keeps at least {_days(r['donor_days_after_plan'])} of stock.")
        rows.append(" ".join(parts))
    return rows


def rules_why(items: list[dict]) -> str:
    """The same reason in a fixed sentence, for when no model answers."""
    worst = min(items, key=lambda t: t["rationale"].get("recipient_days_before", 1e9))
    r = worst["rationale"]
    receiver, donor = worst["to"]["name"], worst["from"]["name"]
    if r.get("recipient_days_before") is None or r.get("recipient_days_after_this") is None:
        text = f"{receiver} is short of {worst['sku_name']} and {donor} has stock to spare."
    else:
        text = (
            f"{receiver} has {_days(r['recipient_days_before'])} of {worst['sku_name']} "
            f"left; this trip brings it to {_days(r['recipient_days_after_this'])}."
        )
    if r.get("donor_days_after_plan") is not None:
        text += f" {donor} keeps at least {_days(r['donor_days_after_plan'])}."
    if len(items) > 1:
        text += f" {len(items)} medicines travel on this trip."
    return text


class TransferConflict(Exception):
    """The transfer can no longer be carried out as proposed."""


async def decide_transfer(
    session: AsyncSession,
    transfer_id: int,
    decision: Literal["approved", "rejected"],
    *,
    actor_ref: str,
    actor_role: str,
    channel: str = "web",
) -> Transfer | None:
    """Record an officer's decision. Approval moves the stock.

    Approval re-checks the donor against its *current* stock, not the stock at
    planning time: if a field report has since lowered it, sending the planned
    quantity could now breach the donor's floor, so the approval is refused and
    the officer is told to re-plan.
    """
    # populate_existing forces a real SELECT ... FOR UPDATE. Without it, get()
    # returns an object already in the session and takes no lock, and two
    # simultaneous approvals could both move the stock.
    t = await session.get(
        Transfer, transfer_id, with_for_update=True, populate_existing=True
    )
    if t is None:
        return None
    if t.status != "proposed":
        raise TransferConflict(f"Transfer {transfer_id} was already {t.status}.")

    if decision == "approved":
        donor = await session.get(
            FacilitySkuState, (t.from_facility, t.sku_code),
            with_for_update=True, populate_existing=True,
        )
        recipient = await session.get(
            FacilitySkuState, (t.to_facility, t.sku_code),
            with_for_update=True, populate_existing=True,
        )
        if donor is None or recipient is None:
            raise TransferConflict("Stock position for this transfer no longer exists.")
        qty = float(t.qty or 0)
        floor_units = settings.donor_floor_days * donor.daily_burn_rate
        if donor.qty_on_hand - qty < floor_units - 1e-6:
            raise TransferConflict(
                f"Donor stock has changed since planning: sending {qty:.0f} would leave "
                f"{donor.qty_on_hand - qty:.0f}, below its {settings.donor_floor_days:.0f}-day "
                f"floor of {floor_units:.0f}. Re-run the plan."
            )

        now = datetime.now(timezone.utc)
        # Approval dispatches the batch: the stock leaves the donor now, and
        # the recipient is credited only when the delivery is confirmed
        # (movements.confirm_receipt). Crediting both ends here would make the
        # map show medicine that is still on a truck, and would leave nothing
        # for the receipt side of the ledger to check.
        session.add(
            StockReading(
                facility_id=t.from_facility, sku_code=t.sku_code,
                qty_on_hand=donor.qty_on_hand - qty, reported_at=now,
                source="transfer", confidence=1.0,
                raw_payload={"transfer_id": t.id, "direction": "out", "qty": qty},
            )
        )
        donor_facility = await session.get(Facility, t.from_facility)
        await movements.record_dispatch(
            session,
            batch_id=f"TRF-{t.id}",
            sku_code=t.sku_code,
            from_ref=t.from_facility,
            to_facility=t.to_facility,
            state_silo=donor_facility.state_silo if donor_facility else "",
            qty=qty,
            dispatched_at=now,
            dispatch_source="transfer",
            transfer_id=t.id,
            # The planned drive time, plus the loading and paperwork the plan
            # already accounts for, plus a working day of slack.
            transit_hours=float(t.eta_hours or 0) + 24.0,
        )

    t.status = decision
    session.add(
        Approval(
            transfer_id=t.id, actor_ref=actor_ref, actor_role=actor_role,
            decision=decision, channel=channel,
        )
    )
    await session.commit()
    return t

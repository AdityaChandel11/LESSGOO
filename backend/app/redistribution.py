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
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import numpy as np
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from . import maps, movements, outbreak, services
from .movements import OPEN as MOVEMENT_OPEN, RECEIVED as MOVEMENT_RECEIVED, SHORT as MOVEMENT_SHORT
from .config import settings
from .models import (
    Approval,
    Facility,
    FacilitySkuState,
    MedicineMovement,
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
class Promise:
    """Stock already asked for in an open request: it leaves `from_id` and
    reaches `to_id` if the donor says yes."""

    from_id: str
    to_id: str
    qty: float


# Only the solver's own proposals are a re-plan's to replace. A centre's own
# request (FACILITY_REQUEST), and anything of unknown origin, is work
# somebody is waiting on, and a re-plan leaves it alone (fix list #37).
SOLVER_PROPOSAL = "threshold"
# A transfer a pharmacist asked for. Written on insert and read by the caps,
# the reply window and the sandbox cleanup, so it is spelled once, here.
FACILITY_REQUEST = "facility_request"

INDIA = timezone(timedelta(hours=5, minutes=30))


def request_window() -> timedelta:
    """How long a centre's request waits for the donor's reply (fix list #31)."""
    if settings.demo_mode:
        return timedelta(minutes=settings.demo_request_reply_minutes)
    return timedelta(hours=settings.request_reply_hours)


def request_lapsed(created_at: datetime, now: datetime, window: timedelta) -> bool:
    """A request nobody answered in time. Derived, like a movement's
    "overdue": nothing writes it, so nothing has to keep it true."""
    return created_at + window <= now


def lapsed_message(lapsed_at: datetime, requester: str) -> str:
    return (
        "This request lapsed at {0} (India time) with no reply. Ask {1} to send it again."
    ).format(lapsed_at.astimezone(INDIA).strftime("%H:%M"), requester)


def replaceable_on_replan(triggered_by: str | None) -> bool:
    return triggered_by == SOLVER_PROPOSAL


def after_promises(nodes: list[StockNode], promises: list[Promise]) -> list[StockNode]:
    """Each centre's stock as the solver should see it: a donor's less what it
    has already been asked for, a receiver's plus what is already owed to it.

    Without this the solver can offer the same spare stock twice — against
    spec 12.3's "never propose taking a donor below its own safety stock" —
    and plan a second delivery to a centre whose first is still pending.
    Status is re-derived from the adjusted cover with no trust multiplier; it
    only orders and weights recipients, and never decides eligibility.
    """
    outgoing: dict[str, float] = defaultdict(float)
    incoming: dict[str, float] = defaultdict(float)
    for p in promises:
        outgoing[p.from_id] += p.qty
        incoming[p.to_id] += p.qty
    adjusted: list[StockNode] = []
    for n in nodes:
        qty = max(0.0, n.qty - outgoing.get(n.facility_id, 0.0) + incoming.get(n.facility_id, 0.0))
        if qty == n.qty:
            adjusted.append(n)
            continue
        days = qty / n.burn if n.burn > 0 else n.days
        adjusted.append(replace(n, qty=qty, days=days, status=services.classify(days)))
    return adjusted


def replacement_note(
    key: tuple[str, str, str], replaced: dict[tuple[str, str, str], int], at: datetime
) -> dict:
    """What a new proposal carries when it repeats one the re-plan removed
    (same donor, receiver and medicine), so the donor sees it was updated
    rather than finding a different card under their thumb."""
    old = replaced.get(key)
    return {} if old is None else {"replaces_transfer": old, "updated_at": at.isoformat()}


def replaced_message(at: datetime) -> str:
    return (
        "This recommendation was replaced when the plan was recomputed at {0} (India "
        "time). The list now shows the current one."
    ).format(at.astimezone(INDIA).strftime("%H:%M"))


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


def _outbreak_note(
    p: Proposal,
    surges: dict[tuple[str, str], "outbreak.Surge"],
    unsurged: dict[tuple[str, str], StockNode],
) -> dict:
    """Why a trip is pre-positioning: which outbreak, how big, on what basis,
    and the recipient's cover without it."""
    surge = surges.get((p.to_id, p.sku_code))
    if surge is None:
        return {}
    base = unsurged.get((p.to_id, p.sku_code))
    days = round(base.qty / base.burn, 2) if base is not None and base.burn > 0 else None
    return {"outbreak": surge.rationale(days)}


# ================================================= oversight (fix #39) ===
# The Redistribution tab is oversight, not an approval queue: the donor
# centre decides every trip, and officers watch the pipeline and act on its
# exceptions. Everything below is counted from the rows, bounded by one state
# and OVERSIGHT_DAYS.

OVERSIGHT_DAYS = 30


def pipeline(transfers: list[dict], movements: list[dict]) -> dict[str, int]:
    """Where every recommendation and request of the window stands now."""
    by_status = defaultdict(int)
    for t in transfers:
        by_status[t["status"]] += 1
    moved = defaultdict(int)
    for m in movements:
        moved[m["status"]] += 1
    return {
        "recommended": len(transfers),
        "awaiting_donor": by_status["proposed"],
        "accepted": by_status["approved"] + by_status["completed"],
        "declined": by_status["rejected"],
        "withdrawn": by_status["cancelled"],
        "in_transit": moved[MOVEMENT_OPEN],
        "received": moved[MOVEMENT_RECEIVED] + moved[MOVEMENT_SHORT] + moved["over"],
        # Received in full: the receipt matched the dispatch.
        "verified": moved[MOVEMENT_RECEIVED],
    }


def would_lift(proposals: list[dict], critical_days: float) -> int:
    """Receivers the open plan takes from under the critical line to over it,
    if every donor accepts — a promise, not an outcome, and worded as one."""
    lifted = {
        p["to"]
        for p in proposals
        if p["status"] == "proposed"
        and p.get("recipient_days_before") is not None
        and p["recipient_days_before"] < critical_days
        and (p.get("recipient_days_after_plan") or 0) >= critical_days
    }
    return len(lifted)


def no_reply(transfers: list[dict], now: datetime, window: timedelta) -> list[dict]:
    """Proposals still waiting for the donor after the reply window."""
    return [t for t in transfers if t["status"] == "proposed" and now - t["created_at"] > window]


def not_received(movements: list[dict], now: datetime) -> list[dict]:
    """Deliveries still on the road after the day they were expected."""
    return [m for m in movements if m["status"] == MOVEMENT_OPEN and m["expected_by"] < now]


def cross_district_trips(transfers: list[dict], district_of: dict[str, str]) -> int:
    """Open trips whose donor and receiver sit in different districts (fix
    #38). A trip is one donor-receiver pair, whatever it carries. A centre
    whose district is not known is not called cross-district."""
    pairs = {
        (t["from"], t["to"])
        for t in transfers
        if t["status"] == "proposed"
    }
    return sum(
        1
        for src, dst in pairs
        if src in district_of and dst in district_of and district_of[src] != district_of[dst]
    )


async def oversight(session: AsyncSession, state: str, now: datetime) -> dict:
    """The officer's view of one state's redistribution, from the rows."""
    since = now - timedelta(days=OVERSIGHT_DAYS)
    in_state = select(Facility.id).where(Facility.state_silo == state)
    names = {
        fid: (name, district)
        for fid, name, district in (
            await session.execute(
                select(Facility.id, Facility.name, Facility.district).where(
                    Facility.state_silo == state
                )
            )
        ).all()
    }
    sku_names = {s.code: s.name for s in (await session.execute(select(Sku))).scalars()}

    transfers = [
        {
            "id": t.id, "status": t.status, "triggered_by": t.triggered_by, "to": t.to_facility,
            "from": t.from_facility, "sku": t.sku_code, "qty": float(t.qty or 0),
            "created_at": t.created_at,
            "recipient_days_before": (t.rationale or {}).get("recipient_days_before"),
            "recipient_days_after_plan": (t.rationale or {}).get("recipient_days_after_plan"),
        }
        for t in (
            await session.execute(
                select(Transfer).where(
                    Transfer.to_facility.in_(in_state), Transfer.created_at >= since
                )
            )
        ).scalars()
    ]
    ids = [t["id"] for t in transfers]
    movements = (
        [
            {
                "id": m.id, "status": m.status, "transfer_id": m.transfer_id,
                "to": m.to_facility, "sku": m.sku_code, "batch": m.batch_id,
                "qty": float(m.qty_dispatched), "expected_by": m.expected_by,
            }
            for m in (
                await session.execute(
                    select(MedicineMovement).where(MedicineMovement.transfer_id.in_(ids))
                )
            ).scalars()
        ]
        if ids
        else []
    )

    solver_open = [
        t for t in transfers if t["status"] == "proposed" and t["triggered_by"] == SOLVER_PROPOSAL
    ]
    computed_at = max((t["created_at"] for t in solver_open), default=None)
    reports_since = 0
    if computed_at is not None:
        reports_since = int(
            await session.scalar(
                select(func.count())
                .select_from(StockReading)
                .where(
                    StockReading.reported_at > computed_at,
                    StockReading.facility_id.in_(in_state),
                )
            )
            or 0
        )

    # Exceptions read from the map's stored rows for this state: the
    # controlled medicines the solver never touches, and centres under the
    # critical line that no open trip reaches.
    short_rows = (
        await session.execute(
            select(
                FacilitySkuState.facility_id, FacilitySkuState.sku_code,
                FacilitySkuState.days_of_stock, Sku.is_controlled,
            )
            .join(Sku, Sku.code == FacilitySkuState.sku_code)
            .where(
                FacilitySkuState.facility_id.in_(in_state),
                FacilitySkuState.status.in_(("critical", "at_risk")),
            )
        )
    ).all()
    reached = {
        (t["to"], t["sku"]) for t in transfers if t["status"] in ("proposed", "approved")
    }

    def where(fid: str) -> dict:
        name, district = names.get(fid, (fid, ""))
        return {"facility_id": fid, "name": name, "district": district}

    controlled = [
        {**where(fid), "sku_code": sku, "sku_name": sku_names.get(sku, sku),
         "days": round(days, 1) if days is not None else None}
        for fid, sku, days, is_controlled in short_rows
        if is_controlled
    ]
    unreached = [
        {**where(fid), "sku_code": sku, "sku_name": sku_names.get(sku, sku),
         "days": round(days, 1) if days is not None else None}
        for fid, sku, days, is_controlled in short_rows
        if not is_controlled
        and days is not None
        and days < settings.critical_days
        and (fid, sku) not in reached
    ]
    unreached.sort(key=lambda r: r["days"] if r["days"] is not None else 1e9)

    # The real reply window, not the demo's minutes-long one: an officer's
    # "no reply" means a donor centre has sat on it for a working day.
    reply_window = timedelta(hours=settings.request_reply_hours)

    def trip(t: dict) -> dict:
        return {
            "transfer_id": t["id"], "sku_code": t["sku"], "sku_name": sku_names.get(t["sku"], t["sku"]),
            "qty": t["qty"], "from": where(t["from"]) if t["from"] in names else {"facility_id": t["from"], "name": t["from"], "district": ""},
            "to": where(t["to"]), "created_at": t["created_at"],
        }

    return {
        "state": state,
        "window_days": OVERSIGHT_DAYS,
        "recommended_open": len(solver_open),
        "requests_open": sum(
            1 for t in transfers if t["status"] == "proposed" and t["triggered_by"] != SOLVER_PROPOSAL
        ),
        "computed_at": computed_at,
        "reports_since": reports_since,
        "would_lift": would_lift(
            [t for t in transfers if t["status"] == "proposed"], settings.critical_days
        ),
        "critical_days": settings.critical_days,
        # Fix #38: how many open trips cross a district line — so
        # "cross-district" on screen is a count, not a claim.
        "cross_district_open": cross_district_trips(
            transfers, {fid: district for fid, (_name, district) in names.items()}
        ),
        "pipeline": pipeline(transfers, movements),
        "no_reply": [trip(t) for t in no_reply(transfers, now, reply_window)][:20],
        "no_reply_total": len(no_reply(transfers, now, reply_window)),
        "reply_window_hours": settings.request_reply_hours,
        "not_received": [
            {"movement_id": m["id"], "transfer_id": m["transfer_id"], "batch": m["batch"],
             "sku_name": sku_names.get(m["sku"], m["sku"]), "qty": m["qty"],
             "to": where(m["to"]), "expected_by": m["expected_by"]}
            for m in not_received(movements, now)
        ][:20],
        "not_received_total": len(not_received(movements, now)),
        "declined": [trip(t) for t in transfers if t["status"] == "rejected"][:20],
        "controlled": controlled[:20],
        "controlled_total": len(controlled),
        "unreached": unreached[:20],
        "unreached_total": len(unreached),
    }


@dataclass
class PlanResult:
    state: str
    sku: str | None
    solver: str
    generated_at: datetime
    transfer_ids: list[int]
    unmet: list[dict]
    manual_review: list[dict]
    # The solver's own earlier proposals this run removed, so anyone acting on
    # one can be told it was replaced, and when (see replaced_message).
    replaced_ids: list[int] = field(default_factory=list)


async def open_promises(
    session: AsyncSession, state: str, sku: str | None = None
) -> dict[str, list[Promise]]:
    """Stock already asked for in open requests touching this state, per
    medicine. Bounded by status and state; open requests are few by design
    (workspace caps them)."""
    in_state = select(Facility.id).where(Facility.state_silo == state)
    stmt = select(Transfer.sku_code, Transfer.from_facility, Transfer.to_facility, Transfer.qty).where(
        Transfer.status == "proposed",
        Transfer.triggered_by.is_distinct_from(SOLVER_PROPOSAL),
        Transfer.to_facility.in_(in_state) | Transfer.from_facility.in_(in_state),
    )
    if sku:
        stmt = stmt.where(Transfer.sku_code == sku)
    out: dict[str, list[Promise]] = defaultdict(list)
    for code, src, dst, qty in (await session.execute(stmt)).all():
        out[code].append(Promise(src, dst, float(qty or 0)))
    return out


async def generate_plan(
    session: AsyncSession, state: str, sku: str | None = None
) -> PlanResult:
    """Replace the solver's own open proposals for this state with a freshly
    solved plan.

    Only the solver's own proposals are replaced (replaceable_on_replan): a
    centre's request is work somebody is waiting on and survives any number of
    re-plans, and the stock it already promises is taken off the donor before
    the solver sees it (after_promises). Decided transfers (approved or
    rejected) are history and are never touched. One plan per state at a
    time: a second run for the same state waits for the first to commit, so
    two runs cannot interleave their deletes and inserts.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "plan:" + state}
    )
    rules = PlanRules.from_settings()
    skus = {s.code: s for s in (await session.execute(select(Sku))).scalars().all()}
    promises = await open_promises(session, state, sku)
    nodes_by_sku = {
        code: after_promises(nodes, promises.get(code, []))
        for code, nodes in (await load_state_nodes(session, state, sku)).items()
    }
    # Spec 12.5: an active outbreak is a temporary multiplier on the burn of
    # the medicines it drives in its district — the same solver then sees the
    # shorter cover and proposes pre-positioning trips (fix #41).
    surges = await outbreak.surges_for_state(session, state, datetime.now(timezone.utc))
    if sku:
        surges = {k: v for k, v in surges.items() if k[1] == sku}
    unsurged = {
        (n.facility_id, code): n for code, ns in nodes_by_sku.items() for n in ns
    }
    nodes_by_sku = outbreak.apply_surges(
        nodes_by_sku, {k: v.multiplier for k, v in surges.items()}
    )

    in_state = select(Facility.id).where(Facility.state_silo == state)
    replaceable = select(
        Transfer.id, Transfer.from_facility, Transfer.to_facility, Transfer.sku_code
    ).where(
        Transfer.status == "proposed",
        Transfer.triggered_by == SOLVER_PROPOSAL,
        Transfer.to_facility.in_(in_state),
    )
    if sku:
        replaceable = replaceable.where(Transfer.sku_code == sku)
    replaced = {
        (src, dst, code): tid for tid, src, dst, code in (await session.execute(replaceable)).all()
    }
    if replaced:
        # Re-checked at delete time: a donor's decision that committed while
        # this plan was solving has moved the row out of 'proposed', and the
        # delete leaves it alone.
        await session.execute(
            delete(Transfer).where(
                Transfer.id.in_(list(replaced.values())), Transfer.status == "proposed"
            )
        )
    replaced_at = datetime.now(timezone.utc)

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
            triggered_by=SOLVER_PROPOSAL,
            rationale={
                **p.rationale,
                **replacement_note((p.from_id, p.to_id, p.sku_code), replaced, replaced_at),
                **_outbreak_note(p, surges, unsurged),
            },
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
        replaced_ids=sorted(replaced.values()),
    )


async def list_transfers(
    session: AsyncSession,
    *,
    state: str | None = None,
    statuses: list[str] | None = None,
    ids: list[int] | None = None,
    from_facility: str | None = None,
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
    if from_facility:
        stmt = stmt.where(Transfer.from_facility == from_facility)
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
                # Transfers never cross a state line; the receiver's is the trip's.
                "state": d.state_silo,
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


# ------------------------------------------------- what the card cannot say ---
# Fix #46. The trip card already shows the quantities and the days of cover;
# an explanation that repeats them tells nobody anything. These are the facts
# behind the choice, worked out after the plan from the same rows the solver
# read: the nearer centre that was not used and why, when the receiver runs
# out if nobody sends, how fresh the donor's figure is, and whether the
# distance is a road route or an estimate.


def _pair_km(a: StockNode, b: StockNode, road_factor: float) -> float:
    lat1, lng1, lat2, lng2 = map(np.radians, (a.lat, a.lng, b.lat, b.lng))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(h)) * road_factor)


def alternative_donor(
    receiver: StockNode,
    donor: StockNode,
    nodes: list[StockNode],
    rules: PlanRules,
    committed: dict[str, float],
) -> dict | None:
    """The nearest centre to the receiver that is closer than the chosen
    donor, and the reason the plan did not use it. None when the donor is
    already the nearest centre holding the medicine.

    `nodes` are the state's centres for this medicine; `committed` is what
    each already owes to other open trips. The reason is read from the same
    figures the solver used — it is never guessed.
    """
    reach = _pair_km(receiver, donor, rules.road_factor)
    closer = [
        (km, n)
        for n in nodes
        if n.facility_id not in (receiver.facility_id, donor.facility_id)
        and (km := _pair_km(receiver, n, rules.road_factor)) < reach
    ]
    if not closer:
        return None
    km, n = min(closer, key=lambda pair: pair[0])
    lead = "{0} is closer (about {1} km)".format(n.name, round(km))
    spare = max(0.0, n.qty - n.burn * rules.donor_floor_days)
    if n.days is not None and n.days < rules.trigger_days:
        reason = "short_itself"
        sentence = "{0} but is itself short: {1} of stock.".format(lead, _days(n.days))
    elif n.days is not None and n.days <= rules.donor_floor_days:
        reason = "below_floor"
        sentence = "{0} but holds {1} of stock, below the {2:g}-day floor a donor must keep.".format(
            lead, _days(n.days), rules.donor_floor_days
        )
    elif spare - committed.get(n.facility_id, 0.0) < rules.min_units:
        reason = "committed"
        sentence = "{0}, but its spare stock is already promised to other trips in this plan.".format(lead)
    else:
        reason = "could_spare"
        sentence = (
            "{0} and could also spare stock; the plan is solved for the whole state at once, "
            "and this trip is part of its cheapest overall answer."
        ).format(lead)
    return {
        "facility_id": n.facility_id, "name": n.name, "district": n.district,
        "km": round(km, 1), "days": n.days, "reason": reason, "sentence": sentence,
    }


SOURCE_WORDS = {
    "form": "entered on the stock form",
    "voice": "spoken into the app",
    "sms": "reported by SMS",
    "ivr": "reported by phone call",
    "whatsapp": "reported by WhatsApp",
    "photo": "read from a photographed document",
    "transfer": "updated by a confirmed delivery",
    "seed": "a seeded demonstration figure",
}


def trip_facts(
    *,
    sku_name: str,
    receiver: StockNode,
    donor: StockNode,
    alternative: dict | None,
    receiver_runs_out_on: date | None,
    today: date,
    donor_counted_on: date | None,
    donor_source: str | None,
    receiver_trust: tuple[int, str] | None,
    route_source: str | None,
    road_factor: float,
    requested: bool = False,
) -> list[str]:
    """What a first-time reader needs and the card does not show, as plain
    sentences. Each is omitted when its figure is not known. `requested` is a
    centre's own request: the receiver picked the donor, the plan did not."""
    from .workspace import day_words  # workspace imports this module

    if requested:
        why = "{0} chose this donor itself, from the nearest centres that could spare {1}.".format(
            receiver.name, sku_name
        )
    elif alternative:
        why = alternative["sentence"]
    else:
        why = "{0} is the nearest centre that holds {1} above its floor.".format(donor.name, sku_name)
    out = [why]
    if receiver_runs_out_on is not None:
        when = day_words(receiver_runs_out_on)
        out.append(
            "Without this delivery {0} runs out of {1} around {2}.".format(receiver.name, sku_name, when)
            if receiver_runs_out_on >= today
            else "At its usual use {0} would already have run out around {1}; its count is "
            "overdue.".format(receiver.name, when)
        )
    if donor_counted_on is not None:
        out.append(
            "{0}'s figure is its count of {1}, {2}.".format(
                donor.name, day_words(donor_counted_on), SOURCE_WORDS.get(donor_source or "", "reported")
            )
        )
    if receiver_trust is not None:
        out.append(
            "{0}'s data confidence is {1} out of 100 ({2}).".format(receiver.name, *receiver_trust)
        )
    if route_source != "google_routes":
        out.append(
            "The distance is a straight-line estimate multiplied by {0:g}, not a road route.".format(
                road_factor
            )
        )
    return out


def planned_by(items: list[dict]) -> str:
    """Who decided this trip, read from the trip itself: the solver that ran
    (it falls back to a greedy one when OR-Tools is unavailable), or a centre
    that asked another for stock."""
    first = items[0]
    if first.get("triggered_by") == FACILITY_REQUEST:
        return "This is a request one centre raised to another, not a recommendation of the state plan."
    solver = (first.get("rationale") or {}).get("solver")
    name = {"ortools": "OR-Tools", "greedy": "the greedy fallback solver"}.get(
        solver, "the redistribution solver"
    )
    return "The plan is computed by {0}.".format(name)


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
    # Re-checked under the row lock, so a cancel or a lapse that lands while
    # the donor is deciding wins (fix list #31).
    if t.status == "cancelled":
        raise TransferConflict("This request was cancelled by the centre that raised it.")
    if t.status != "proposed":
        raise TransferConflict(f"Transfer {transfer_id} was already {t.status}.")
    if t.triggered_by == FACILITY_REQUEST:
        window = request_window()
        if request_lapsed(t.created_at, datetime.now(timezone.utc), window):
            requester = await session.get(Facility, t.to_facility)
            raise TransferConflict(
                lapsed_message(t.created_at + window, requester.name if requester else "the centre")
            )

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

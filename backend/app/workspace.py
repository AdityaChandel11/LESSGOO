"""The pharmacist's workspace — one facility, answered from its own rows.

This module exists so that `services.py` keeps its one audience (national
rollups for the map) and `redistribution.py` keeps its one job (solving a
state's plan). What a single pharmacist needs is a third question — "what do I
hold, when does it run out, where do I get more, and what did I already ask
for" — and answering it inside either of those would give both of them a
second reader to keep happy.

The split within this file is the one `redistribution.py` already uses, and it
is deliberate: every decision is a pure function over plain values, and the
only impure things are two small queries bounded by a single facility. The
pure half is unit-tested in `tests/test_workspace.py`; the impure half is
exercised end to end against the real database by `checks/workspace.py`.

Two rules run through all of it:

  * A figure nobody verified is never described as verified. `seed` rows are
    synthetic baseline and `transfer` rows are stock moving on its own; calling
    either one "counted by hand" would invent a check that never happened.
  * A number that cannot be computed is absent, not guessed. No burn rate means
    no stock-out date — not today, and not never.

Distances here are haversine multiplied by `road_factor`, never a road route:
`MAPS_MODE` is `osm` and no Routes credential is configured, so every distance
this module produces carries `straight_line_x1.3` and the UI says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .redistribution import EARTH_RADIUS_KM, PlanRules, StockNode, split_roles

# The one string this module uses to mark a transfer as a pharmacist's own
# request. It is written on insert and read by both caps and by the sandbox
# cleanup, so it lives here once rather than being spelled out three times.
FACILITY_REQUEST = "facility_request"

# What every distance in this module is. Named rather than described, so a
# screen cannot accidentally imply a road route (spec: route_source).
STRAIGHT_LINE = "straight_line_x1.3"

# Reading sources, grouped by what they actually prove about a number.
_IN_APP = frozenset({"form", "voice", "photo"})
_PHONE = frozenset({"sms", "ivr", "whatsapp"})
_SYSTEM = frozenset({"seed", "transfer"})

ProvenanceKind = Literal["counted", "delivery", "phone", "system", "none"]


# =============================================================== values ===


@dataclass(frozen=True)
class SkuRule:
    """The parts of a medicine that change what may be done with it.

    A plain value rather than the `Sku` model so the rules can be tested
    without a database session, and so nothing here can lazily load a relation.
    """

    code: str
    name: str
    unit: str
    is_controlled: bool
    cold_chain: bool


@dataclass(frozen=True)
class LastReceipt:
    batch_id: str
    qty_received: float
    received_at: datetime
    received_via: str


@dataclass(frozen=True)
class Provenance:
    kind: ProvenanceKind
    at: datetime | None
    days_ago: int | None
    detail: str


@dataclass(frozen=True)
class Donor:
    facility_id: str
    name: str
    district: str
    lat: float
    lng: float
    km: float
    distance_basis: str
    spare_units: int
    days_kept: float


@dataclass(frozen=True)
class Supply:
    sku_code: str
    units_needed: int
    donors: list[Donor]
    manual_only: bool
    reason: str | None


@dataclass(frozen=True)
class CapLimits:
    per_facility: int
    overall: int

    @classmethod
    def from_settings(cls) -> "CapLimits":
        return cls(
            per_facility=settings.max_open_requests_per_facility,
            overall=settings.max_open_facility_requests_global,
        )


@dataclass(frozen=True)
class CapVerdict:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class DeliveryEstimate:
    expected_on: date
    travel_hours: float
    basis: str
    label: str
    assumptions: dict[str, float | int]


class RequestRefused(Exception):
    """The request cannot be raised as asked. The message is shown to the user,
    so it names the rule and the number, never an internal condition."""


# ========================================================== provenance ===


def _describe(source: str | None, receipt: LastReceipt | None) -> tuple[ProvenanceKind, str]:
    if receipt is not None:
        return "delivery", "Batch {0}, {1:,.0f} received".format(
            receipt.batch_id, receipt.qty_received
        )
    if source in _IN_APP:
        detail = {
            "form": "Entered on the stock form",
            "voice": "Spoken into the app and transcribed",
            "photo": "Read from a photographed register",
        }[source]
        return "counted", detail
    if source in _PHONE:
        return "phone", "Reported by {0}".format(
            {"sms": "SMS", "ivr": "phone call", "whatsapp": "WhatsApp"}[source]
        )
    if source in _SYSTEM:
        detail = (
            "Opening figure from the synthetic baseline — no field report yet"
            if source == "seed"
            else "Adjusted by an approved transfer, not a field report"
        )
        return "system", detail
    return "none", "No report has been received for this medicine"


def provenance(
    last_source: str | None,
    last_reported_at: datetime | None,
    last_receipt: LastReceipt | None,
    now: datetime,
) -> Provenance:
    """How this facility's figure for one medicine was last checked, and when.

    The newest of the two independent signals wins: a stock report the facility
    made, and a delivery it confirmed. They are deliberately separate records —
    the two-sided ledger of spec 26.3 exists precisely so that neither side can
    quietly settle for the other — so the more recent one is the better answer
    to "how do you know?".
    """
    reading_at = last_reported_at
    receipt_at = last_receipt.received_at if last_receipt else None

    if reading_at is None and receipt_at is None:
        kind, detail = _describe(None, None)
        return Provenance(kind=kind, at=None, days_ago=None, detail=detail)

    # A `seed` or `transfer` row is not a verification, so it never outranks a
    # confirmed delivery however recent it is. This is not a tie-breaker but
    # the rule itself: confirming a receipt writes a `transfer` reading at the
    # same instant, and letting that row win would mean the one medicine
    # somebody physically counted onto the shelf reported as unverified.
    reading_verifies = last_source is not None and last_source not in _SYSTEM
    receipt_wins = receipt_at is not None and (
        reading_at is None or not reading_verifies or receipt_at > reading_at
    )

    if receipt_wins:
        at, kind_detail = receipt_at, _describe(None, last_receipt)
    else:
        at, kind_detail = reading_at, _describe(last_source, None)

    kind, detail = kind_detail
    return Provenance(
        kind=kind,
        at=at,
        days_ago=max(0, (now - at).days) if at else None,
        detail=detail,
    )


# ======================================================= stockout date ===


def stockout_date(
    days_of_stock: float | None, last_reported_at: datetime | None
) -> date | None:
    """The day cover runs out, counted from the reading it was measured at.

    Absent when either input is missing. A facility with no measured usage has
    no stock-out date — not today, and not never. This is the same rule
    `services.classify` follows for status: absence of evidence must not
    manufacture an alert.
    """
    if days_of_stock is None or last_reported_at is None:
        return None
    return (last_reported_at + timedelta(days=days_of_stock)).date()


# ========================================================= find supply ===


def road_km_between(a: StockNode, b: StockNode, road_factor: float) -> float:
    """One pair's estimated road km. `redistribution.road_km` builds a whole
    matrix with numpy, which is the right shape for the solver and the wrong
    shape here: this asks about one recipient, so the loop is the cheaper read."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h)) * road_factor


def rank_donors(
    nodes: list[StockNode],
    rules: PlanRules,
    *,
    recipient_id: str,
    sku: SkuRule,
    limit: int = 3,
) -> Supply:
    """The nearest facilities that can spare this medicine without dropping
    below their own floor.

    This is `split_roles` read from the recipient's side, so a donor offered
    here is a donor the optimiser would also accept — the pharmacist is never
    shown an option an officer would then have to refuse.
    """
    if sku.is_controlled:
        # Spec 12.3 and research.md Red Team A: controlled substances are
        # excluded from the solver entirely and escalate to a person.
        return Supply(
            sku_code=sku.code,
            units_needed=0,
            donors=[],
            manual_only=True,
            reason=(
                "Controlled substance. Transfers of this medicine are arranged "
                "by the district drug store against a signed requisition, never "
                "proposed automatically."
            ),
        )

    donors, recipients = split_roles(nodes, rules)
    me = next((n for n in nodes if n.facility_id == recipient_id), None)
    needed = next((u for n, u in recipients if n.facility_id == recipient_id), 0)

    if me is None:
        return Supply(sku.code, 0, [], False, "This centre does not stock this medicine.")
    if needed <= 0:
        return Supply(
            sku.code, 0, [], False,
            "This centre is not short of this medicine, so no transfer is needed.",
        )

    max_km = rules.cold_chain_max_km if sku.cold_chain else rules.max_km
    offers: list[Donor] = []
    for n, spare in donors:
        if n.facility_id == recipient_id:
            continue
        km = road_km_between(n, me, rules.road_factor)
        if km > max_km:
            continue
        offers.append(
            Donor(
                facility_id=n.facility_id,
                name=n.name,
                district=n.district,
                lat=n.lat,
                lng=n.lng,
                km=round(km, 1),
                distance_basis=STRAIGHT_LINE,
                spare_units=spare,
                days_kept=round((n.qty - spare) / n.burn, 2) if n.burn else 0.0,
            )
        )

    offers.sort(key=lambda d: d.km)
    reason = None
    if not offers:
        reason = (
            "No centre within {0:.0f} km can spare this medicine without dropping "
            "below its own {1:.0f}-day safety stock.".format(max_km, rules.donor_floor_days)
        )
    return Supply(sku.code, needed, offers[:limit], False, reason)


# =============================================================== caps ===


def check_caps(open_here: int, open_overall: int, limits: CapLimits) -> CapVerdict:
    """Two caps, because they stop two different things.

    The per-facility one keeps any single pharmacist's queue reviewable by the
    officer who has to read it. The global one is the database size guard: a
    judge clicking "Request stock" is otherwise an unbounded writer against a
    1 GB volume. Both count only facility-originated requests, so the solver's
    own proposals can never lock a pharmacist out of asking.

    The per-facility limit is reported first when both are breached, because it
    is the only one the person reading the message can do anything about.
    """
    if open_here >= limits.per_facility:
        return CapVerdict(
            False,
            "This centre already has {0} open requests, which is the limit of {1}. "
            "Confirm or cancel one before raising another.".format(
                open_here, limits.per_facility
            ),
        )
    if open_overall >= limits.overall:
        return CapVerdict(
            False,
            "Limit reached: {0} requests are open across the platform, which is the "
            "demonstration cap of {1}. Existing requests must be settled before new "
            "ones can be raised.".format(open_overall, limits.overall),
        )
    return CapVerdict(True)


# ======================================================= request rules ===


def validate_request(
    donor: StockNode, qty: float, *, sku: SkuRule, rules: PlanRules
) -> None:
    """Refuse a request that could not lawfully or safely be approved.

    `decide_transfer` re-checks the floor against the donor's *current* stock at
    approval, and must: stock moves between asking and deciding. Checking here
    as well is not redundant — it is the difference between a pharmacist being
    told now and an officer discovering it later with the request already in
    their queue.
    """
    if sku.is_controlled:
        raise RequestRefused(
            "{0} is a controlled substance and cannot be requested through this "
            "screen. Raise a signed requisition with the district drug store.".format(sku.name)
        )
    if qty < rules.min_units:
        raise RequestRefused(
            "The smallest transfer worth moving is {0} {1}. Ask for at least that "
            "much, or obtain it locally.".format(rules.min_units, sku.unit)
        )
    if donor.burn <= 0:
        raise RequestRefused(
            "{0} has no measured usage for this medicine, so there is no safe "
            "quantity to take from it.".format(donor.name)
        )
    floor_units = rules.donor_floor_days * donor.burn
    if donor.qty - qty < floor_units - 1e-6:
        raise RequestRefused(
            "Sending {0:,.0f} would leave {1} with {2:,.0f} {3}, below its "
            "{4:.0f}-day floor of {5:,.0f}. Ask for less, or choose another centre.".format(
                qty, donor.name, donor.qty - qty, sku.unit,
                rules.donor_floor_days, floor_units,
            )
        )


def reference(transfer_id: int) -> str:
    """The number a pharmacist reads down a phone line. Derived from the
    transfer id rather than stored, so it cannot drift from the row it names."""
    return "SS-{0:06d}".format(transfer_id)


# ================================================== delivery estimate ===


def delivery_estimate(*, km: float, raised_at: datetime) -> DeliveryEstimate:
    """When the medicine could realistically arrive, and what that assumes.

    Every constant is a setting, and every one of them is returned alongside
    the date so the screen can show its own arithmetic. This is an estimate
    from a straight-line distance, and it says so twice: in `label` and in
    `basis`. It is never a scheduled time, because nothing here books a vehicle.
    """
    assumptions: dict[str, float | int] = {
        "avg_speed_kmh": settings.avg_speed_kmh,
        "handling_hours": settings.handling_hours,
        "road_factor": settings.road_factor,
        "dispatch_cutoff_hour": settings.dispatch_cutoff_hour,
        "working_hours_per_day": settings.working_hours_per_day,
    }
    travel_hours = km / settings.avg_speed_kmh + settings.handling_hours

    # A request raised after the cutoff leaves the next morning; district
    # stores do not load vehicles at night.
    start = raised_at.date()
    if raised_at.hour >= settings.dispatch_cutoff_hour:
        start += timedelta(days=1)
    days_on_the_road = math.ceil(travel_hours / settings.working_hours_per_day)

    return DeliveryEstimate(
        expected_on=start + timedelta(days=max(1, days_on_the_road)),
        travel_hours=round(travel_hours, 2),
        basis=STRAIGHT_LINE,
        label="estimate",
        assumptions=assumptions,
    )


# ====================================================== bounded reads ===
# Everything below touches the database. Each query is bounded by one facility
# or by one (state, sku) pair; none of them may grow into a national scan.


async def last_receipts(session: AsyncSession, facility_id: str) -> dict[str, LastReceipt]:
    """Most recent settled delivery per medicine, for one facility.

    DISTINCT ON keeps this to a single index scan over
    ix_movements_facility_time rather than a query per medicine. Bounded by one
    facility on purpose: the national equivalent of this read is the one
    measured at over 300 seconds and ~70 MB of temp in docs/STORAGE_NOTES.md.
    """
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT ON (sku_code)
                   sku_code, batch_id, qty_received, received_at, received_via
            FROM medicine_movements
            WHERE to_facility = :fid AND received_at IS NOT NULL
            ORDER BY sku_code, received_at DESC
            """
        ),
        {"fid": facility_id},
    )
    return {
        r[0]: LastReceipt(
            batch_id=r[1], qty_received=float(r[2]), received_at=r[3], received_via=r[4]
        )
        for r in rows.all()
    }


async def open_request_counts(session: AsyncSession, facility_id: str) -> tuple[int, int]:
    """(open requests for this facility, open requests everywhere).

    Counts only `triggered_by = 'facility_request'`. The solver's own proposals
    are not a pharmacist's queue and must not consume their allowance.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE to_facility = :fid) AS here,
                    count(*)                                   AS overall
                FROM transfers
                WHERE status = 'proposed' AND triggered_by = :tag
                """
            ),
            {"fid": facility_id, "tag": FACILITY_REQUEST},
        )
    ).first()
    return (int(row[0]), int(row[1])) if row else (0, 0)

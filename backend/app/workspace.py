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

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import services
from .config import settings
from .models import FacilityBriefing
from .redistribution import (
    EARTH_RADIUS_KM,
    FACILITY_REQUEST,  # re-exported: api, checks and scripts read it from here
    INDIA,
    PlanRules,
    StockNode,
    request_lapsed,
    request_window,
    split_roles,
)

# What every distance in this module is. Named rather than described, so a
# screen cannot accidentally imply a road route (spec: route_source).
STRAIGHT_LINE = "straight_line_x1.3"

# Reading sources, grouped by what they actually prove about a number. A photo
# is not a count: it is a document a model read, and what it proves depends on
# which document it was (fix list #11).
_IN_APP = frozenset({"form", "voice"})
_PHONE = frozenset({"sms", "ivr", "whatsapp"})
_SYSTEM = frozenset({"seed", "transfer"})

ProvenanceKind = Literal["counted", "photo", "delivery", "phone", "system", "none"]

_DOCUMENT_LABEL = {
    "stock_count": "Stock count",
    "issue_record": "Issue record",
    "delivery_slip": "Delivery slip",
}


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
    # The approval the estimate depends on: the dispatch cutoff on the day
    # the batch would leave. "If approved by 14:00 today, about 1 Oct" —
    # never a flat date, because nothing moves until the donor says yes.
    approve_by: datetime


class RequestRefused(Exception):
    """The request cannot be raised as asked. The message is shown to the user,
    so it names the rule and the number, never an internal condition."""


# ========================================================== provenance ===


def _photo_detail(document: dict | None) -> str:
    """Which document a photo reading came from, and who read it. Readings
    from before fix #11 carry no document type and say so."""
    doc = document or {}
    kind = _DOCUMENT_LABEL.get(doc.get("document_type"))
    if kind is None:
        return "Read from a photo; the kind of document was not recorded"
    reader = (
        "the test reader (no model was called)" if doc.get("read_by") == "mock" else "Gemini"
    )
    return "{0} read by {1}".format(kind, reader)


def _describe(
    source: str | None, receipt: LastReceipt | None, document: dict | None = None
) -> tuple[ProvenanceKind, str]:
    if receipt is not None:
        return "delivery", "Batch {0}, {1:,.0f} received".format(
            receipt.batch_id, receipt.qty_received
        )
    if source == "photo":
        return "photo", _photo_detail(document)
    if source in _IN_APP:
        detail = {
            "form": "Entered on the stock form",
            "voice": "Spoken into the app and transcribed",
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
    document: dict | None = None,
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
        at, kind_detail = reading_at, _describe(last_source, None, document)

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


# ================================================== cover, as of now ===
# Stored cover is measured at the last count; the clock never moved it, so a
# card could say "5.4 days, at risk, runs out today" (fix #26). The countdown
# is applied where the card is built, never written back to stored rows.

COUNT_OVERDUE = "count_overdue"


@dataclass(frozen=True)
class Cover:
    days_of_stock: float | None
    status: str
    count_overdue: bool


def cover_now(
    days_of_stock: float | None,
    last_reported_at: datetime | None,
    status: str,
    now: datetime,
    warning_multiplier: float = 1.0,
) -> Cover:
    """Stored cover minus the days since the count (floor 0), reclassified.

    Past the projected run-out the shelf is "count overdue": its real level is
    unknown and probably empty, and the honest instruction is to count it.
    """
    if days_of_stock is None or last_reported_at is None:
        return Cover(days_of_stock, status, False)
    elapsed = max(0.0, (now - last_reported_at).total_seconds() / 86400.0)
    left = days_of_stock - elapsed
    if left <= 0:
        return Cover(0.0, COUNT_OVERDUE, True)
    return Cover(left, services.classify(left, warning_multiplier), False)


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec")


def day_words(d: date) -> str:
    """24 Sept — the way the cards and the fix list write a date."""
    return "{0} {1}".format(d.day, MONTHS[d.month - 1])


# ===================================================== use and forecast ===
# Fix #84: the forecast made visible. A medicine's last 28 days of use, the
# burn rate, and the shared model's next-7-day forecast where the model
# covers that state and medicine. The web service carries no torch, so the
# model's coverage is mirrored in services.py; tests/test_forecast_visible.py
# keeps it in step with federation/pytorchexample.

MODEL_STATES = services.MODEL_STATES
MODEL_SKUS = services.MODEL_SKUS


@dataclass(frozen=True)
class DayUse:
    day: date
    # None when no count covers that day: use is never invented.
    used: float | None
    # True when the figure is a decline spread evenly over the days between
    # two counts more than a day apart, rather than one day's own count.
    spread: bool = False


def daily_use(
    series: list[tuple[datetime, float, str | None]], now: datetime, window: int = 28
) -> list[DayUse]:
    """Each decline between two counts, spread evenly over the days it covers —
    the same arithmetic as the burn rate, which divides by elapsed days. A
    restock is not negative use, and stock that left on a transfer was not
    used."""
    days = [(now - timedelta(days=window - 1 - i)).date() for i in range(window)]
    used: dict[date, float] = {}
    spread: set[date] = set()
    for (prev_at, prev_qty, _), (at, qty, source) in zip(series, series[1:]):
        if source == "transfer":
            continue
        start, end = prev_at.date(), at.date()
        covered = [end - timedelta(days=k) for k in range((end - start).days)] or [end]
        share = max(0.0, prev_qty - qty) / len(covered)
        for d in covered:
            used[d] = used.get(d, 0.0) + share
            if len(covered) > 1:
                spread.add(d)
    return [DayUse(d, used.get(d), d in spread) for d in days]


def forecast_note(
    *, in_model: bool, fresh: bool, published: datetime | None, version: str | None
) -> str:
    if not in_model:
        return "Burn rate — this state or medicine is not in the shared model yet."
    if fresh and published is not None:
        return (
            "Next 7 days from the shared model, trained across {0} states ({1}) without "
            "moving their rows · {2}, published {3}"
        ).format(len(MODEL_STATES), ", ".join(MODEL_STATES), version, day_words(published.date()))
    if published is not None:
        return (
            "This state and medicine are in the shared model, but its latest forecast here "
            "(published {0}) is older than {1:g} days, so the burn rate is used."
        ).format(day_words(published.date()), settings.forecast_max_age_days)
    return (
        "This state and medicine are in the shared model, but no forecast has been published "
        "for this centre yet, so the burn rate is used."
    )


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
            "This centre already has {0} requests waiting for a reply, which is the "
            "limit of {1}. Cancel one from its medicine card, or wait: a request with "
            "no reply lapses on its own.".format(open_here, limits.per_facility),
        )
    if open_overall >= limits.overall:
        return CapVerdict(
            False,
            "Limit reached: {0} requests were raised across the platform in the last 24 "
            "hours, which is the demonstration cap of {1}. Try again later.".format(
                open_overall, limits.overall
            ),
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
    # stores do not load vehicles at night. `raised_at` must be local time
    # (in_india): the cutoff is a local hour.
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
        approve_by=datetime.combine(
            start, time(settings.dispatch_cutoff_hour), tzinfo=raised_at.tzinfo
        ),
    )


def in_india(moment: datetime) -> datetime:
    """A stored UTC time as the clock on a centre's wall reads it."""
    return moment.astimezone(INDIA)


def request_words(status: str, lapsed: bool, donor: str) -> str:
    """A request's state as a pharmacist says it — never `proposed` or
    `donor_facility` (fix list #31)."""
    if status == "proposed":
        return "No reply from {0}".format(donor) if lapsed else (
            "Awaiting reply from {0} (pharmacist)".format(donor)
        )
    return {
        "approved": "Accepted and sent by {0}".format(donor),
        "rejected": "Declined by {0}".format(donor),
        "cancelled": "Cancelled by this centre",
        "completed": "Delivered from {0}".format(donor),
    }.get(status, "Closed")


# =========================================================== briefing ===
# "What should I do today?", answered from this centre's own position.
#
# The deterministic answer below is the default on every screen and needs no
# credential. It is written and tested before the model path exists, so the
# fallback is never the untested branch — when Gemini is unavailable, out of
# quota, or switched off, this is what renders, with no AI label on it.
#
# Nothing here invents a figure. Every number in the output came in through
# `BriefingRow`, and a medicine with no cover figure is described without one
# rather than given a fabricated day count.


@dataclass(frozen=True)
class BriefingRow:
    """One medicine, reduced to what a briefing may talk about."""

    sku_name: str
    unit: str
    qty: float
    days_of_stock: float | None
    status: str
    # Set when the count is overdue (fix #26): the count's date, and the day
    # the shelf would have run out at the usual use.
    last_counted_on: date | None = None
    ran_out_on: date | None = None


def _worst(rows: list[BriefingRow]) -> BriefingRow | None:
    """The medicine that runs out first. Rows with no cover figure cannot be
    ranked and are never chosen as the headline."""
    measured = [r for r in rows if r.days_of_stock is not None]
    if not measured:
        return None
    return min(measured, key=lambda r: r.days_of_stock)


def _days(value: float) -> str:
    """One decimal place, matching what the medicine cards already show."""
    return "{0:.1f}".format(value)


def rules_briefing(rows: list[BriefingRow]) -> dict[str, str]:
    """One line of guidance in English and Hindi, computed, not generated.

    Deliberately blunt about ordering rather than clinical: this is a stock
    screen, and a sentence about medicine that strayed into treatment would be
    both wrong and outside what this system is allowed to say.
    """
    if not rows:
        return {
            "en": "No stock has been reported for this centre yet, so there is "
                  "nothing to act on. Report today's counts to start.",
            "hi": "इस केंद्र के लिए अभी तक कोई स्टॉक दर्ज नहीं हुआ है। आज की "
                  "गिनती दर्ज करें।",
        }

    overdue = [r for r in rows if r.status == COUNT_OVERDUE]
    if overdue:
        first = min(overdue, key=lambda r: r.ran_out_on or date.max)
        others = len(overdue) - 1
        counted = day_words(first.last_counted_on) if first.last_counted_on else "an earlier day"
        ran_out = day_words(first.ran_out_on) if first.ran_out_on else "before today"
        tail_en = (
            " {0} other medicine{1} also overdue for a count.".format(
                others, " is" if others == 1 else "s are"
            )
            if others > 0
            else ""
        )
        tail_hi = " {0} और दवाओं की गिनती भी बाकी है।".format(others) if others > 0 else ""
        return {
            "en": "Count the {0} shelf today — last counted {1:,.0f} {2} on {3}; at your "
                  "usual use it would have run out around {4}.{5}".format(
                      first.sku_name, first.qty, first.unit, counted, ran_out, tail_en
                  ),
            "hi": "{0} की अलमारी आज गिनें — आखिरी गिनती {3} को {1:,.0f} {2} थी; सामान्य "
                  "खपत पर यह लगभग {4} तक खत्म हो गई होगी।{5}".format(
                      first.sku_name, first.qty, first.unit, counted, ran_out, tail_hi
                  ),
        }

    worst = _worst(rows)
    if worst is None:
        return {
            "en": "None of this centre's medicines has enough reporting history "
                  "to estimate cover. Report today's counts to start.",
            "hi": "इस केंद्र की किसी दवा का पर्याप्त रिकॉर्ड नहीं है, इसलिए "
                  "अनुमान नहीं लगाया जा सकता। आज की गिनती दर्ज करें।",
        }

    days = _days(worst.days_of_stock)
    critical = [r for r in rows if r.status == "critical"]
    at_risk = [r for r in rows if r.status == "at_risk"]

    if critical:
        others = len(critical) - 1
        tail_en = (
            " {0} other medicine{1} also needs ordering.".format(
                others, "" if others == 1 else "s"
            )
            if others > 0
            else ""
        )
        tail_hi = (
            " {0} और दवाओं का ऑर्डर भी ज़रूरी है।".format(others) if others > 0 else ""
        )
        return {
            "en": "Order {0} today — about {1} days of cover left.{2}".format(
                worst.sku_name, days, tail_en
            ),
            "hi": "{0} का ऑर्डर आज ही दें — लगभग {1} दिन का स्टॉक बचा है।{2}".format(
                worst.sku_name, days, tail_hi
            ),
        }

    if at_risk:
        return {
            "en": "Raise a request for {0} this week — about {1} days of cover "
                  "left.".format(worst.sku_name, days),
            "hi": "{0} के लिए इस सप्ताह अनुरोध करें — लगभग {1} दिन का स्टॉक "
                  "बचा है।".format(worst.sku_name, days),
        }

    return {
        "en": "Nothing needs ordering today. {0} is the tightest at about {1} "
              "days of cover.".format(worst.sku_name, days),
        "hi": "आज कुछ भी ऑर्डर करने की ज़रूरत नहीं। सबसे कम {0} है, लगभग {1} "
              "दिन का स्टॉक।".format(worst.sku_name, days),
    }


def briefing_hash(rows: list[BriefingRow]) -> str:
    """A fingerprint of the position a briefing describes.

    The cache is keyed on this as well as on the day, so advice expires when
    the thing it describes changes rather than only when the clock says so.

    Sorted by name, and cover rounded to a tenth of a day: a burn rate that
    drifts in the sixth decimal is not a new situation, and treating it as one
    would spend a model call every time somebody opened the tab.
    """
    parts = sorted(
        "{0}|{1}|{2}".format(
            r.sku_name,
            "none" if r.days_of_stock is None else "{0:.1f}".format(r.days_of_stock),
            r.status,
        )
        for r in rows
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ============================================================== to-do ===
# Fix #85: the Today card as a prioritised list, not one sentence about one
# medicine. Every line is computed from this centre's own rows, in an order a
# rule decides, so it needs no key and reads the same whoever opens it. The
# model's job (vision.write_briefing) is to say the same list in plainer
# words and in the state's language; it may not add a line or a figure.


@dataclass(frozen=True)
class TodoDelivery:
    """One consignment on its way to this centre."""

    sku_name: str
    unit: str
    qty: float
    from_name: str
    expected_on: date
    overdue: bool


@dataclass(frozen=True)
class TodoRequest:
    """A request this centre raised that nobody has answered yet."""

    sku_name: str
    unit: str
    qty: float
    donor: str


@dataclass(frozen=True)
class TodoItem:
    kind: str
    en: str
    hi: str
    # The workspace tab where the thing is done.
    tab: str


@dataclass(frozen=True)
class StateLanguage:
    code: str
    name: str
    native: str


# The principal official language of a state, where it is not Hindi. A centre
# in any other state gets English and Hindi only.
STATE_LANGUAGES: dict[str, StateLanguage] = {
    "MH": StateLanguage("mr", "Marathi", "मराठी"),
    "KL": StateLanguage("ml", "Malayalam", "മലയാളം"),
    "TN": StateLanguage("ta", "Tamil", "தமிழ்"),
    "KA": StateLanguage("kn", "Kannada", "ಕನ್ನಡ"),
    "AP": StateLanguage("te", "Telugu", "తెలుగు"),
    "TG": StateLanguage("te", "Telugu", "తెలుగు"),
    "WB": StateLanguage("bn", "Bengali", "বাংলা"),
    "GJ": StateLanguage("gu", "Gujarati", "ગુજરાતી"),
    "PB": StateLanguage("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    "OD": StateLanguage("or", "Odia", "ଓଡ଼ିଆ"),
    "AS": StateLanguage("as", "Assamese", "অসমীয়া"),
}

MAX_TODO = 7


def state_language(state_code: str) -> StateLanguage | None:
    return STATE_LANGUAGES.get(state_code)


def _qty(value: float) -> str:
    return "{0:,.0f}".format(value)


def todo_items(
    *,
    rows: list[BriefingRow],
    deliveries: list[TodoDelivery],
    awaiting_your_reply: int,
    own_waiting: list[TodoRequest],
    outbreaks: list[str],
    bed_report_today: bool | None,
    checked_in_today: int | None,
) -> list[TodoItem]:
    """Today's list, most urgent first, capped at MAX_TODO.

    The order is the rule: a shelf nobody has counted, then a shelf about to
    empty, then stock that should have arrived, then a neighbour waiting on
    this centre, then an outbreak, then everything that can wait a day.
    `None` for the ward report or the check-in means "not known", and nothing
    is asked for on the strength of not knowing.
    """
    requested = {r.sku_name for r in own_waiting}
    items: list[TodoItem] = []

    overdue = sorted(
        (r for r in rows if r.status == COUNT_OVERDUE),
        key=lambda r: r.ran_out_on or date.max,
    )
    for r in overdue:
        counted = day_words(r.last_counted_on) if r.last_counted_on else "an earlier day"
        ran_out = day_words(r.ran_out_on) if r.ran_out_on else "before today"
        items.append(TodoItem(
            "recount",
            "Recount {0}: last counted {1} {2} on {3}; at your usual use it would have "
            "run out around {4}.".format(r.sku_name, _qty(r.qty), r.unit, counted, ran_out),
            "{0} दोबारा गिनें: आखिरी गिनती {3} को {1} {2} थी; सामान्य खपत पर यह लगभग {4} "
            "तक खत्म हो गई होगी।".format(r.sku_name, _qty(r.qty), r.unit, counted, ran_out),
            "medicines",
        ))

    def by_cover(status: str) -> list[BriefingRow]:
        return sorted(
            (r for r in rows if r.status == status and r.sku_name not in requested),
            key=lambda r: r.days_of_stock if r.days_of_stock is not None else 1e9,
        )

    for r in by_cover("critical"):
        cover_en = (
            ", about {0} days of cover".format(_days(r.days_of_stock))
            if r.days_of_stock is not None else ""
        )
        cover_hi = (
            ", लगभग {0} दिन का स्टॉक".format(_days(r.days_of_stock))
            if r.days_of_stock is not None else ""
        )
        items.append(TodoItem(
            "order",
            "Order {0} today: {1} {2} left{3}.".format(r.sku_name, _qty(r.qty), r.unit, cover_en),
            "{0} का ऑर्डर आज ही दें: {1} {2} बचा है{3}।".format(
                r.sku_name, _qty(r.qty), r.unit, cover_hi
            ),
            "medicines",
        ))

    late = sorted((d for d in deliveries if d.overdue), key=lambda d: d.expected_on)
    for d in late:
        when = day_words(d.expected_on)
        items.append(TodoItem(
            "delivery_overdue",
            "Delivery overdue: {0} {1} of {2} from {3}, expected {4}. If it has arrived, "
            "count it and confirm it on Orders.".format(
                _qty(d.qty), d.unit, d.sku_name, d.from_name, when
            ),
            "डिलीवरी में देरी: {3} से {2} की {0} {1}, {4} तक आनी थी। आ चुकी हो तो गिनकर "
            "ऑर्डर टैब पर पुष्टि करें।".format(_qty(d.qty), d.unit, d.sku_name, d.from_name, when),
            "orders",
        ))

    if awaiting_your_reply > 0:
        one = awaiting_your_reply == 1
        items.append(TodoItem(
            "reply_needed",
            "{0} request{1} for your stock {2} waiting for your answer on Orders.".format(
                awaiting_your_reply, "" if one else "s", "is" if one else "are"
            ),
            "आपके स्टॉक के लिए {0} अनुरोध आपके उत्तर की प्रतीक्षा में {1} — ऑर्डर टैब "
            "देखें।".format(awaiting_your_reply, "है" if one else "हैं"),
            "orders",
        ))

    for headline in outbreaks:
        items.append(TodoItem(
            "outbreak",
            "{0}. Check your cover for its medicines.".format(headline),
            "ज़िले में प्रकोप की सूचना: {0}। इसकी दवाओं का स्टॉक जाँचें।".format(headline),
            "medicines",
        ))

    for r in by_cover("at_risk"):
        if r.days_of_stock is None:
            continue
        items.append(TodoItem(
            "order_soon",
            "Request {0} this week: about {1} days of cover left.".format(
                r.sku_name, _days(r.days_of_stock)
            ),
            "{0} के लिए इस सप्ताह अनुरोध करें: लगभग {1} दिन का स्टॉक बचा है।".format(
                r.sku_name, _days(r.days_of_stock)
            ),
            "medicines",
        ))

    for d in sorted((d for d in deliveries if not d.overdue), key=lambda d: d.expected_on):
        when = day_words(d.expected_on)
        items.append(TodoItem(
            "delivery_arriving",
            "Expect {0} {1} of {2} from {3} by {4}. Count it before you confirm.".format(
                _qty(d.qty), d.unit, d.sku_name, d.from_name, when
            ),
            "{3} से {2} की {0} {1} {4} तक आने वाली है। पुष्टि से पहले गिन लें।".format(
                _qty(d.qty), d.unit, d.sku_name, d.from_name, when
            ),
            "orders",
        ))

    for q in own_waiting:
        items.append(TodoItem(
            "request_waiting",
            "Your request for {0} {1} of {2} from {3} is awaiting a reply.".format(
                _qty(q.qty), q.unit, q.sku_name, q.donor
            ),
            "{3} से {2} की {0} {1} का आपका अनुरोध उत्तर की प्रतीक्षा में है।".format(
                _qty(q.qty), q.unit, q.sku_name, q.donor
            ),
            "orders",
        ))

    if bed_report_today is False:
        items.append(TodoItem(
            "bed_report",
            "No ward bed report has been sent today. Today's code is on the Beds tab.",
            "आज वार्ड की बेड रिपोर्ट नहीं भेजी गई है। आज का कोड बेड टैब पर है।",
            "beds",
        ))

    if checked_in_today == 0:
        items.append(TodoItem(
            "check_in",
            "No staff check-in has been recorded at this centre today.",
            "आज इस केंद्र पर किसी कर्मचारी की उपस्थिति दर्ज नहीं हुई है।",
            "attendance",
        ))

    if not items:
        line = rules_briefing(rows)
        return [TodoItem("all_clear", line["en"], line["hi"], "medicines")]
    return items[:MAX_TODO]


def todo_hash(items: list[TodoItem]) -> str:
    """A fingerprint of the list a briefing describes: the cached wording
    expires when the list changes, not only when the clock says so."""
    return hashlib.sha256("\n".join(i.en for i in items).encode("utf-8")).hexdigest()


# ====================================================== bounded reads ===
# Everything below touches the database. Each query is bounded by one facility
# or by one (state, sku) pair; none of them may grow into a national scan.


async def last_receipts(session: AsyncSession, facility_id: str) -> dict[str, LastReceipt]:
    """Most recent settled delivery per medicine, for one facility.

    DISTINCT ON keeps this to a single index scan over
    ix_movements_facility_time rather than a query per medicine. Bounded by one
    facility on purpose: the national equivalent of this read is the one
    measured at over 300 seconds and ~70 MB of temp files on the deployed database.
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


async def last_count_times(
    session: AsyncSession, facility_id: str, *, since: datetime
) -> dict[str, datetime]:
    """When each medicine's shelf level was last stated here, per medicine.

    Any reading but a delivery's own ledger row (`transfer`) states a level; a
    superseded reading states nothing. Bounded by one facility and a window,
    over ix_readings_facility_sku_time. Used to refuse a photographed document
    dated before the latest count, which that count already includes (fix #11).
    """
    rows = await session.execute(
        text(
            """
            SELECT sku_code, MAX(reported_at)
            FROM stock_readings
            WHERE facility_id = :fid AND reported_at >= :since
              AND source <> 'transfer' AND superseded_by IS NULL
            GROUP BY sku_code
            """
        ),
        {"fid": facility_id, "since": since},
    )
    return {r[0]: r[1] for r in rows.all()}


async def photo_documents(
    session: AsyncSession, facility_id: str, latest: dict[str, datetime]
) -> dict[str, dict]:
    """The document behind each medicine's latest reading, where that reading
    was a photo: `latest` maps medicine to that reading's time. Only this
    facility's photo rows at those times are read, so the card can say "Issue
    record read by Gemini" instead of guessing what the photo was."""
    if not latest:
        return {}
    rows = await session.execute(
        text(
            """
            SELECT sku_code, reported_at, raw_payload
            FROM stock_readings
            WHERE facility_id = :fid AND source = 'photo'
              AND reported_at >= :since AND sku_code = ANY(:skus)
            """
        ),
        {"fid": facility_id, "since": min(latest.values()), "skus": list(latest)},
    )
    return {
        sku: payload or {}
        for sku, at, payload in rows.all()
        if at == latest.get(sku)
    }


async def open_request_counts(
    session: AsyncSession, facility_id: str, *, now: datetime | None = None
) -> tuple[int, int]:
    """(requests this facility is still waiting on, requests raised anywhere in
    the last 24 hours).

    The first is the per-centre cap, and counts only live requests: one that
    lapsed without a reply stops counting, which is what locked Nashik PHC 1
    out when nothing ever expired (fix list #31). The second is the database
    size guard, and counts every request raised in a rolling day whatever
    became of it, so growth stays bounded at the cap a day and the guard frees
    itself without anything having to write. Both count only
    `triggered_by = 'facility_request'`: the solver's own proposals are not a
    pharmacist's queue.
    """
    now = now or datetime.now(timezone.utc)
    live_since = now - request_window()
    day_ago = now - timedelta(hours=24)
    row = (
        await session.execute(
            text(
                """
                SELECT
                    count(*) FILTER (
                        WHERE to_facility = :fid AND status = 'proposed'
                          AND created_at > :live_since
                    ) AS here,
                    count(*) FILTER (WHERE created_at > :day_ago) AS overall
                FROM transfers
                WHERE triggered_by = :tag AND created_at > :oldest
                """
            ),
            {
                "fid": facility_id,
                "tag": FACILITY_REQUEST,
                "live_since": live_since,
                "day_ago": day_ago,
                "oldest": min(live_since, day_ago),
            },
        )
    ).first()
    return (int(row[0]), int(row[1])) if row else (0, 0)


@dataclass(frozen=True)
class OwnRequest:
    """A request this centre raised, as its medicine card shows it."""

    transfer_id: int
    sku_code: str
    qty: float
    from_facility: str
    from_name: str
    status: str
    lapsed: bool
    words: str
    created_at: datetime
    lapses_at: datetime


async def own_requests(
    session: AsyncSession, facility_id: str, *, now: datetime | None = None
) -> list[OwnRequest]:
    """What this centre asked for in the last day, newest first. Bounded by the
    centre, the request tag and the day."""
    now = now or datetime.now(timezone.utc)
    window = request_window()
    rows = await session.execute(
        text(
            """
            SELECT t.id, t.sku_code, t.qty, t.from_facility, f.name, t.status, t.created_at
            FROM transfers t JOIN facilities f ON f.id = t.from_facility
            WHERE t.to_facility = :fid AND t.triggered_by = :tag AND t.created_at > :since
            ORDER BY t.created_at DESC
            """
        ),
        {"fid": facility_id, "tag": FACILITY_REQUEST, "since": now - timedelta(hours=24)},
    )
    out: list[OwnRequest] = []
    for tid, sku, qty, src, name, status, created in rows.all():
        lapsed = status == "proposed" and request_lapsed(created, now, window)
        out.append(
            OwnRequest(
                transfer_id=tid, sku_code=sku, qty=float(qty or 0), from_facility=src,
                from_name=name, status=status, lapsed=lapsed,
                words=request_words(status, lapsed, name), created_at=created,
                lapses_at=created + window,
            )
        )
    return out


def briefing_rows_from_skus(skus: list[BriefingRow]) -> list[str]:
    """The stock position as lines a model may read.

    Built here so the prompt can only ever contain figures that are already on
    the pharmacist's screen. Worst first, and capped: a model given twelve
    medicines writes about the wrong one, and the tail of a healthy list adds
    tokens without adding advice.
    """
    ranked = sorted(
        skus,
        key=lambda r: (
            STATUS_ORDER_FOR_BRIEFING.get(r.status, 9),
            r.days_of_stock if r.days_of_stock is not None else 1e9,
        ),
    )
    lines = []
    for r in ranked[:BRIEFING_MAX_ROWS]:
        if r.status == COUNT_OVERDUE:
            lines.append(
                "- {0}: last counted {1:,.0f} {2} on {3}, count overdue (would have run "
                "out around {4} at usual use)".format(
                    r.sku_name, r.qty, r.unit,
                    day_words(r.last_counted_on) if r.last_counted_on else "an earlier day",
                    day_words(r.ran_out_on) if r.ran_out_on else "before today",
                )
            )
            continue
        cover = (
            "no cover estimate yet"
            if r.days_of_stock is None
            else "{0} days of cover".format(_days(r.days_of_stock))
        )
        lines.append(
            "- {0}: {1:,.0f} {2}, {3}, {4}".format(
                r.sku_name, r.qty, r.unit, cover, r.status.replace("_", " ")
            )
        )
    return lines


STATUS_ORDER_FOR_BRIEFING = {COUNT_OVERDUE: -1, "critical": 0, "at_risk": 1, "healthy": 2}
BRIEFING_MAX_ROWS = 6


async def cached_briefing(
    session: AsyncSession, facility_id: str, lang: str, inputs_hash: str
) -> FacilityBriefing | None:
    """A stored line, if it is still good.

    Two conditions, and both matter. The clock one stops a day-old line being
    shown forever; the hash one stops a line surviving the delivery that made
    it wrong. Without the second, "order ORS today" would still be on screen
    after the ORS arrived.
    """
    row = await session.get(FacilityBriefing, (facility_id, lang))
    if row is None or row.inputs_hash != inputs_hash:
        return None
    age = datetime.now(timezone.utc) - row.generated_at
    if age > timedelta(hours=settings.briefing_ttl_hours):
        return None
    return row


async def store_briefing(
    session: AsyncSession,
    facility_id: str,
    texts: dict[str, str],
    *,
    inputs_hash: str,
    model: str,
) -> None:
    """Write both languages and hold the table to its ceiling.

    One model call produces both languages, so they are written together and
    expire together — a screen that showed a fresh English line beside a stale
    Hindi one would be worse than showing neither.

    The composite primary key already caps a facility at one row per language; this evicts
    across facilities once `max_briefing_rows` is reached, oldest first,
    because this is a cache and the oldest line is the one nobody is reading.
    """
    now = datetime.now(timezone.utc)
    for lang, body in texts.items():
        await session.merge(
            FacilityBriefing(
                facility_id=facility_id,
                lang=lang,
                body=body,
                inputs_hash=inputs_hash,
                model=model,
                generated_at=now,
            )
        )
    await session.flush()

    total = await session.scalar(select(func.count()).select_from(FacilityBriefing))
    excess = int(total or 0) - settings.max_briefing_rows
    if excess > 0:
        doomed = (
            select(FacilityBriefing.facility_id, FacilityBriefing.lang)
            .order_by(FacilityBriefing.generated_at)
            .limit(excess)
        )
        keys = [(r[0], r[1]) for r in (await session.execute(doomed)).all()]
        for facility, lang in keys:
            row = await session.get(FacilityBriefing, (facility, lang))
            if row is not None:
                await session.delete(row)
        await session.flush()

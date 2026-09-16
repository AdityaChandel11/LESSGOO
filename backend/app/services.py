"""Reorder floor and status engine — spec 12.1.

This is the deterministic floor the whole platform stands on. No model runs
here, and none may: the dashboard must be correct before any forecast exists
(spec Section 1, rule 3, and the v2 red-team finding it came from).

Consumption is not stored directly. It is derived from the decline in
qty_on_hand between consecutive readings, with positive jumps ignored as
restocks. That keeps the burn rate honest against whatever the field actually
reported, through whichever channel reported it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from . import outbreak, trust
from .models import (
    BedStatus,
    Facility,
    FacilitySkuState,
    FacilityTrust,
    Forecast,
    Sku,
    StaffCheckin,
    StockReading,
)

STATUS_HEALTHY = "healthy"
STATUS_AT_RISK = "at_risk"
STATUS_CRITICAL = "critical"

# Ordered best to worst; escalation walks one step toward the end.
STATUS_ORDER = (STATUS_HEALTHY, STATUS_AT_RISK, STATUS_CRITICAL)

EPSILON = 1e-6


def classify(days_of_stock: float | None, warning_multiplier: float = 1.0) -> str:
    """Traffic light from days of cover. An unknown burn rate reads as healthy —
    absence of evidence must not manufacture an alert.

    `warning_multiplier` widens both thresholds for a facility whose data
    cannot be relied on (spec 12.6). A facility whose numbers are doubtful is
    more dangerous, not less, so its warning trips earlier: at a confidence of
    0.4 the 7-day threshold becomes 11.2 days. Quantities are untouched —
    only the moment someone is told.
    """
    if days_of_stock is None:
        return STATUS_HEALTHY
    if days_of_stock < settings.critical_days * warning_multiplier:
        return STATUS_CRITICAL
    if days_of_stock < settings.at_risk_days * warning_multiplier:
        return STATUS_AT_RISK
    return STATUS_HEALTHY


def escalate(status: str, steps: int = 1) -> str:
    idx = STATUS_ORDER.index(status)
    return STATUS_ORDER[min(idx + steps, len(STATUS_ORDER) - 1)]


def worst(statuses: list[str]) -> str:
    """A facility is as bad as its worst tracked commodity."""
    if not statuses:
        return STATUS_HEALTHY
    return max(statuses, key=STATUS_ORDER.index)


@dataclass
class SkuStock:
    sku_code: str
    sku_name: str
    qty_on_hand: float
    daily_burn_rate: float | None
    days_of_stock: float | None
    # Which rule produced daily_burn_rate: "burn_rate" (the last 28 days of
    # readings) or "federated" (the shared model's published forecast). Shown
    # on screen, because a number that changes meaning silently is worse than
    # either rule on its own.
    rate_source: str
    status: str
    is_controlled: bool
    cold_chain: bool
    last_reported_at: datetime | None
    last_source: str | None
    last_confidence: float | None


@dataclass
class FacilitySnapshot:
    id: str
    name: str
    type: str
    district: str
    state_silo: str
    lat: float
    lng: float
    beds_total: int
    beds_occupied: int | None
    bed_occupancy_pct: float | None
    staff_checkin_pct: float | None
    # Data confidence (spec 12.6) and the threshold widening it causes.
    trust_score: float | None
    trust_band: str | None
    warning_multiplier: float
    status: str
    stock_status: str
    escalated: bool
    escalation_reasons: list[str] = field(default_factory=list)
    skus: list[SkuStock] = field(default_factory=list)


def _burn_rate(series: list[tuple[datetime, float, str | None]]) -> float | None:
    """Mean daily consumption from consecutive declines.

    `series` must be ascending by time. Positive deltas are restocks and are
    excluded rather than counted as negative consumption. Declines recorded by
    an outbound transfer are stock that left, not stock that was used, so they
    are excluded too — otherwise a facility that donates would look like it is
    burning through medicine and could flip critical for having helped.
    """
    if len(series) < 2:
        return None
    drops = 0.0
    for (_, prev_qty, _), (_, curr_qty, curr_source) in zip(series, series[1:]):
        if curr_source == "transfer":
            continue
        delta = prev_qty - curr_qty
        if delta > 0:
            drops += delta
    # Divide by elapsed days rather than sample count, so a gap in reporting
    # does not inflate the apparent burn rate.
    span_days = max((series[-1][0] - series[0][0]).total_seconds() / 86400.0, 1.0)
    return drops / span_days


async def get_snapshots(
    session: AsyncSession,
    facility_ids: list[str] | None = None,
) -> list[FacilitySnapshot]:
    """Compute status for every facility (or a subset) in a handful of queries."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=settings.burn_rate_window_days + 1)

    fac_stmt = select(Facility)
    if facility_ids:
        fac_stmt = fac_stmt.where(Facility.id.in_(facility_ids))
    facilities = (await session.execute(fac_stmt)).scalars().all()
    if not facilities:
        return []

    ids = [f.id for f in facilities]
    skus = {s.code: s for s in (await session.execute(select(Sku))).scalars().all()}

    # The map's bulk path reads the materialised score (refreshed by
    # `scripts.trust` from the same `trust.compute()` the drawer calls live).
    # Re-deriving six signals for every facility on every pan is not viable,
    # and this is a copy of that one calculation, never a second one.
    # Raised demand from any outbreak currently running over these facilities
    # (spec 12.5). Applied to whatever rate is in force, so it compounds with
    # the burn rate and with a federated forecast alike.
    surge = await outbreak.active_multipliers(session, ids)

    # Published forecasts, when the switch is on. Reading rows here keeps torch
    # out of the web service entirely: a training job writes, the API reads.
    forecasts: dict[tuple[str, str], float] = {}
    if settings.forecast_mode == "federated":
        fresh = now - timedelta(days=settings.forecast_max_age_days)
        forecasts = {
            (f.facility_id, f.sku_code): f.predicted_daily_use
            for f in (
                await session.execute(
                    select(Forecast).where(
                        Forecast.facility_id.in_(ids), Forecast.computed_at >= fresh
                    )
                )
            ).scalars()
        }

    trust_rows = dict(
        (
            await session.execute(
                select(FacilityTrust.facility_id, FacilityTrust).where(
                    FacilityTrust.facility_id.in_(ids)
                )
            )
        ).all()
    )

    readings_stmt = (
        select(
            StockReading.facility_id,
            StockReading.sku_code,
            StockReading.qty_on_hand,
            StockReading.reported_at,
            StockReading.source,
            StockReading.confidence,
        )
        .where(
            StockReading.facility_id.in_(ids),
            StockReading.reported_at >= window_start,
            StockReading.superseded_by.is_(None),
        )
        .order_by(
            StockReading.facility_id,
            StockReading.sku_code,
            StockReading.reported_at,
        )
    )
    series: dict[tuple[str, str], list[tuple[datetime, float, str | None]]] = defaultdict(list)
    latest_meta: dict[tuple[str, str], tuple[datetime, str | None, float | None]] = {}
    for fid, sku, qty, at, source, conf in (await session.execute(readings_stmt)).all():
        key = (fid, sku)
        series[key].append((at, float(qty), source))
        latest_meta[key] = (at, source, float(conf) if conf is not None else None)

    beds_stmt = (
        select(BedStatus.facility_id, BedStatus.beds_occupied)
        .where(BedStatus.facility_id.in_(ids))
        .order_by(BedStatus.facility_id, BedStatus.recorded_at.desc())
    )
    latest_beds: dict[str, int] = {}
    for fid, occupied in (await session.execute(beds_stmt)).all():
        if fid not in latest_beds and occupied is not None:
            latest_beds[fid] = occupied

    # Staff presence, aggregated to the facility only — never per person (rule 8).
    roster: dict[str, set[str]] = defaultdict(set)
    present: dict[str, set[str]] = defaultdict(set)
    cutoff = now - timedelta(days=1)
    checkin_stmt = select(
        StaffCheckin.facility_id, StaffCheckin.staff_ref, StaffCheckin.checked_in_at
    ).where(StaffCheckin.facility_id.in_(ids))
    for fid, staff_ref, at in (await session.execute(checkin_stmt)).all():
        if not staff_ref:
            continue
        roster[fid].add(staff_ref)
        if at >= cutoff:
            present[fid].add(staff_ref)

    snapshots: list[FacilitySnapshot] = []
    for fac in facilities:
        trust_row = trust_rows.get(fac.id)
        trust_score = float(trust_row.score) if trust_row is not None else None
        multiplier = trust.warning_multiplier(trust_score)
        sku_rows: list[SkuStock] = []
        for sku_code, sku in skus.items():
            pts = series.get((fac.id, sku_code))
            if not pts:
                continue
            qty = pts[-1][1]
            burn = _burn_rate(pts)
            rate_source = "burn_rate"
            # The forecast replaces the burn rate only when one has been
            # published recently for this exact facility and medicine. Every
            # other case — switch off, no row, stale row, failed training run —
            # falls through to the rule that needs nothing but the readings.
            predicted = forecasts.get((fac.id, sku_code))
            if predicted is not None and predicted > 0:
                burn, rate_source = predicted, "federated"

            # Named apart from `multiplier` above on purpose: that one widens
            # the *warning threshold* because the data is doubtful, this one
            # raises the *consumption rate* because an outbreak was declared.
            # Sharing a name shadowed the first and sent None into classify().
            surge_multiplier = surge.get((fac.id, sku_code))
            if surge_multiplier and surge_multiplier > 1.0 and burn is not None:
                # The declaration says this facility will get through more of
                # this medicine than its history suggests. Days of cover fall
                # accordingly, and the solver sees a deficit before anyone has
                # reported a shortage — which is the entire point.
                burn *= surge_multiplier
                rate_source = "outbreak"
            dos = None if burn is None else qty / max(burn, EPSILON)
            at, source, conf = latest_meta[(fac.id, sku_code)]
            sku_rows.append(
                SkuStock(
                    sku_code=sku_code,
                    sku_name=sku.name,
                    qty_on_hand=qty,
                    daily_burn_rate=burn,
                    days_of_stock=dos,
                    rate_source=rate_source,
                    status=classify(dos, multiplier),
                    is_controlled=sku.is_controlled,
                    cold_chain=sku.cold_chain,
                    last_reported_at=at,
                    last_source=source,
                    last_confidence=conf,
                )
            )

        stock_status = worst([s.status for s in sku_rows])

        occupied = latest_beds.get(fac.id)
        occupancy_pct = (
            round(100.0 * occupied / fac.beds_total, 1)
            if occupied is not None and fac.beds_total
            else None
        )

        expected = len(roster.get(fac.id, ()))
        checkin_pct = (
            round(100.0 * len(present.get(fac.id, ())) / expected, 1) if expected else None
        )

        reasons: list[str] = []
        if trust_row is not None and multiplier > 1.01:
            reasons.append(
                f"data confidence {trust_score:.0%}, so this facility is warned "
                f"{multiplier:.1f}x earlier"
            )
        if (
            occupancy_pct is not None
            and occupancy_pct > settings.bed_occupancy_escalate_pct
        ):
            reasons.append(f"bed occupancy {occupancy_pct}%")
        if (
            checkin_pct is not None
            and checkin_pct < settings.staff_checkin_escalate_pct
        ):
            reasons.append(f"staff check-in {checkin_pct}%")

        snapshots.append(
            FacilitySnapshot(
                id=fac.id,
                name=fac.name,
                type=fac.type,
                district=fac.district,
                state_silo=fac.state_silo,
                lat=fac.lat,
                lng=fac.lng,
                beds_total=fac.beds_total,
                beds_occupied=occupied,
                bed_occupancy_pct=occupancy_pct,
                staff_checkin_pct=checkin_pct,
                trust_score=trust_score,
                trust_band=trust_row.band if trust_row is not None else None,
                warning_multiplier=multiplier,
                # The widened threshold has already moved stock_status; a low
                # score must not also escalate the facility a second time.
                status=escalate(stock_status)
                if [r for r in reasons if not r.startswith("data confidence")]
                else stock_status,
                stock_status=stock_status,
                escalated=bool(reasons),
                escalation_reasons=reasons,
                skus=sorted(
                    sku_rows,
                    key=lambda s: (-STATUS_ORDER.index(s.status), s.sku_code),
                ),
            )
        )

    return snapshots


async def refresh_facility_state(session: AsyncSession, facility_id: str) -> None:
    """Recompute one facility's snapshot rows from its reading history.

    Called after every commit so `facility_sku_state` — which the national map
    reads on every pan and zoom — never drifts from the append-only readings
    that remain the source of truth.
    """
    snaps = await get_snapshots(session, facility_ids=[facility_id])
    if not snaps:
        return
    rows = [
        {
            "facility_id": facility_id,
            "sku_code": s.sku_code,
            "qty_on_hand": s.qty_on_hand,
            "daily_burn_rate": s.daily_burn_rate or 0.0,
            "days_of_stock": s.days_of_stock,
            "status": s.status,
            "last_reported_at": s.last_reported_at,
            "last_source": s.last_source,
            "last_confidence": s.last_confidence,
        }
        for s in snaps[0].skus
    ]
    if not rows:
        return
    stmt = pg_insert(FacilitySkuState).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["facility_id", "sku_code"],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "qty_on_hand",
                    "daily_burn_rate",
                    "days_of_stock",
                    "status",
                    "last_reported_at",
                    "last_source",
                    "last_confidence",
                )
            },
        )
    )
    await session.commit()

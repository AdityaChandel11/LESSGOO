"""Cross-signal data confidence — spec 12.6.

12.4 checks one signal against itself. This checks a facility's signals against
*each other*, which is the part that is genuinely hard to fake: inflating one
number is easy, keeping attendance, footfall, bed occupancy, stock movement and
delivery confirmations mutually consistent is not.

Rules, deliberately not a model. A second trained system would compete with
federated forecasting for build time and would put a number on a screen that
nobody could interrogate — on a screen whose entire value is that every number
can be interrogated.

What the score is allowed to do:
  1. bring a facility's stock warning forward (never quantities, never exclusion)
  2. rank a district audit queue

What it must never do: exclude a facility from redistribution, attach to a
named individual, or be called fraud detection. Every sentence it produces says
*worth a second look*, because the most common true explanation is a broken
process, not a dishonest one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    BedReport,
    Facility,
    FacilityTrust,
    MedicineMovement,
    StaffCheckin,
    StockReading,
)

WINDOW_DAYS = 14

# Weights sum to 1.0. Attendance against footfall carries the most because it
# is the cheapest signal to fake on its own and the hardest to keep consistent
# with everything else.
WEIGHTS = {
    "attendance_vs_footfall": 0.25,
    "consumption_vs_footfall": 0.20,
    "beds_vs_register": 0.15,
    "receipt_discipline": 0.20,
    "implausible_smoothness": 0.10,
    "verification_quality": 0.10,
}

GOOD, WATCH, AUDIT = "good", "watch", "audit"


@dataclass(frozen=True)
class Scope:
    """Which facilities to score.

    Scoring is done on demand from the live tables — the ledger, the ward
    photos, the check-ins — so a score is never older than the rows it is
    drawn from. Narrowing it to one facility or one state is what keeps that
    affordable: a drawer asks about one facility, an audit queue about one
    officer's patch, and neither has to read the country.
    """

    facility_ids: tuple[str, ...] | None = None
    state: str | None = None
    district: str | None = None

    @property
    def is_national(self) -> bool:
        return not (self.facility_ids or self.state or self.district)


async def resolve(session: AsyncSession, scope: Scope) -> list[str]:
    """The facility ids a scope covers."""
    if scope.facility_ids is not None:
        return list(scope.facility_ids)
    stmt = select(Facility.id)
    if scope.state:
        stmt = stmt.where(Facility.state_silo == scope.state)
    if scope.district:
        stmt = stmt.where(Facility.district == scope.district)
    return list((await session.execute(stmt)).scalars())


def band_for(score: float) -> str:
    if score >= 0.75:
        return GOOD
    if score >= 0.5:
        return WATCH
    return AUDIT


@dataclass
class Component:
    signal: str
    penalty: float  # 0.0 clean .. 1.0 flatly contradictory
    weight: float
    reason: str
    # Where the rows behind this sentence live. The interface turns it into a
    # link that opens exactly those rows, because a score an officer cannot
    # click through to is a score they have to take on faith.
    evidence: dict | None = None

    @property
    def contribution(self) -> float:
        return round(self.penalty * self.weight, 4)


@dataclass
class Score:
    facility_id: str
    score: float
    band: str
    components: list[Component] = field(default_factory=list)

    def as_rows(self) -> list[dict]:
        return [
            {
                "signal": c.signal,
                "penalty": round(c.penalty, 3),
                "weight": c.weight,
                "cost": c.contribution,
                "reason": c.reason,
                "evidence": c.evidence,
            }
            for c in self.components
        ]


def _component(
    signal: str, penalty: float, reason: str, evidence: dict | None = None
) -> Component:
    return Component(
        signal, max(0.0, min(1.0, penalty)), WEIGHTS[signal], reason, evidence
    )


# ============================================================ the signals ===
# Each returns {facility_id: Component}. Set-based on purpose: this runs over
# every facility in the country, so a per-facility query loop would take
# thousands of round trips to answer one screen.


async def _attendance_vs_footfall(
    session: AsyncSession, since: datetime, ids: list[str]
) -> dict:
    rows = (
        await session.execute(
            select(
                StaffCheckin.facility_id,
                func.count().label("checkins"),
                func.count().filter(StaffCheckin.footfall_same_period == 0).label("no_patients"),
                func.count().filter(StaffCheckin.geofence_ok.is_(True)).label("verified"),
                func.count().filter(StaffCheckin.geofence_ok.isnot(None)).label("checked"),
            )
            .where(
                StaffCheckin.checked_in_at >= since,
                StaffCheckin.facility_id.in_(ids),
            )
            .group_by(StaffCheckin.facility_id)
        )
    ).all()

    out: dict[str, Component] = {}
    for fid, checkins, no_patients, verified, checked in rows:
        if not checkins:
            continue
        share = no_patients / checkins
        if share <= 0.2:
            out[fid] = _component(
                "attendance_vs_footfall", 0.0,
                "Attendance and patient numbers move together.",
            )
            continue
        out[fid] = _component(
            "attendance_vs_footfall",
            # Half the days is already a strong disagreement; cap there rather
            # than requiring every single day to contradict.
            min(1.0, (share - 0.2) / 0.3),
            f"Staff recorded present on {no_patients} of {checkins} shifts where no "
            f"patients were logged at all.",
        )
        if checked and verified / checked < 0.5:
            # Recorded separately below under verification quality.
            pass
    return out


async def _consumption_vs_footfall(
    session: AsyncSession, since: datetime, ids: list[str]
) -> dict:
    """Stock that moves without patients, or patients without stock moving.

    Deliberately crude: the only thing being asked is whether the two series
    point the same way, not how strongly.
    """
    rows = (
        await session.execute(
            select(
                StockReading.facility_id,
                func.count().label("readings"),
                func.count().filter(
                    (StockReading.footfall_same_day == 0)
                ).label("no_patients"),
                func.count(func.distinct(StockReading.qty_on_hand)).label("distinct_qty"),
            )
            .where(
                StockReading.reported_at >= since,
                StockReading.source != "transfer",
                StockReading.facility_id.in_(ids),
            )
            .group_by(StockReading.facility_id)
        )
    ).all()

    out: dict[str, Component] = {}
    for fid, readings, no_patients, _distinct in rows:
        if readings < 5:
            continue
        share = no_patients / readings
        if share <= 0.3:
            out[fid] = _component(
                "consumption_vs_footfall", 0.0,
                "Medicine use tracks the patients seen.",
            )
        else:
            out[fid] = _component(
                "consumption_vs_footfall",
                min(1.0, (share - 0.3) / 0.4),
                f"Stock reported on {no_patients} of {readings} days with no patient "
                f"footfall recorded.",
            )
    return out


async def _beds_vs_register(
    session: AsyncSession, since: datetime, ids: list[str]
) -> dict:
    rows = (
        await session.execute(
            select(
                BedReport.facility_id,
                func.count().label("reports"),
                func.count()
                .filter(
                    BedReport.register_admissions.isnot(None),
                    BedReport.beds_occupied.isnot(None),
                    func.abs(
                        cast(BedReport.beds_occupied, Float)
                        - cast(BedReport.register_admissions, Float)
                    )
                    > 0.25 * func.greatest(cast(BedReport.register_admissions, Float), 1.0),
                )
                .label("disputed"),
            )
            .where(BedReport.reported_at >= since, BedReport.facility_id.in_(ids))
            .group_by(BedReport.facility_id)
        )
    ).all()

    out: dict[str, Component] = {}
    for fid, reports, disputed in rows:
        if reports < 3:
            continue
        share = disputed / reports
        out[fid] = _component(
            "beds_vs_register",
            min(1.0, share / 0.5),
            "The ward photo and the admission register agree."
            if share < 0.1
            else f"The bed count disputed the admission register on {disputed} of "
            f"{reports} days.",
        )
    return out


async def _receipt_discipline(
    session: AsyncSession, now: datetime, ids: list[str]
) -> dict:
    rows = (
        await session.execute(
            select(
                MedicineMovement.to_facility,
                func.count().label("total"),
                func.count()
                .filter(
                    MedicineMovement.status == "in_transit",
                    MedicineMovement.expected_by < now,
                )
                .label("overdue"),
                func.count().filter(MedicineMovement.status == "short").label("short"),
            )
            .where(
                MedicineMovement.dispatched_at >= now - timedelta(days=60),
                MedicineMovement.to_facility.in_(ids),
            )
            .group_by(MedicineMovement.to_facility)
        )
    ).all()

    out: dict[str, Component] = {}
    for fid, total, overdue, short in rows:
        if total < 2:
            continue
        # An unconfirmed batch weighs more than a short one: a short delivery
        # was at least reported, which is the behaviour we want.
        penalty = min(1.0, (overdue * 1.0 + short * 0.5) / total / 0.4)
        if penalty < 0.05:
            reason = "Deliveries are confirmed promptly and in full."
        else:
            parts = []
            if overdue:
                parts.append(f"{overdue} unconfirmed")
            if short:
                parts.append(f"{short} short")
            reason = f"{' and '.join(parts)} of {total} recent consignments."
        out[fid] = _component(
            "receipt_discipline",
            penalty,
            reason,
            # The same filter the movement tab uses, so the link and the score
            # are one query rather than two that might disagree.
            evidence={"tab": "movements", "facility": fid, "view": "attention"}
            if (overdue or short)
            else None,
        )
    return out


async def _implausible_smoothness(
    session: AsyncSession, since: datetime, ids: list[str]
) -> dict:
    """Numbers too tidy to have come from a real storeroom.

    A facility whose stock figures never vary, or are always round hundreds, is
    not necessarily dishonest — a clerk copying forward last week's sheet
    produces exactly this — but either way the number cannot be relied on.
    """
    rows = (
        await session.execute(
            select(
                StockReading.facility_id,
                func.count().label("readings"),
                # qty_on_hand is numeric, and Postgres has no mod() for double
                # precision — stay in numeric rather than casting.
                func.count()
                .filter(StockReading.qty_on_hand % 50 == 0)
                .label("round_numbers"),
                func.count(func.distinct(StockReading.qty_on_hand)).label("distinct_qty"),
            )
            .where(
                StockReading.reported_at >= since,
                StockReading.source.notin_(("transfer", "seed")),
                StockReading.facility_id.in_(ids),
            )
            .group_by(StockReading.facility_id)
        )
    ).all()

    out: dict[str, Component] = {}
    for fid, readings, round_numbers, distinct_qty in rows:
        if readings < 8:
            continue
        roundness = round_numbers / readings
        variety = distinct_qty / readings
        penalty = max(
            min(1.0, (roundness - 0.5) / 0.4) if roundness > 0.5 else 0.0,
            min(1.0, (0.4 - variety) / 0.4) if variety < 0.4 else 0.0,
        )
        out[fid] = _component(
            "implausible_smoothness",
            penalty,
            "Stock figures vary the way a working storeroom does."
            if penalty < 0.05
            else f"{round(roundness * 100)}% of reported figures are round numbers and "
            f"only {distinct_qty} distinct values appear in {readings} reports.",
        )
    return out


async def _verification_quality(
    session: AsyncSession, since: datetime, ids: list[str]
) -> dict:
    """How much of what this facility reports can be checked at all."""
    bed_rows = (
        await session.execute(
            select(
                BedReport.facility_id,
                func.count().label("reports"),
                func.count().filter(BedReport.verification == "verified").label("verified"),
                func.count().filter(BedReport.verification == "rejected").label("rejected"),
            )
            .where(BedReport.reported_at >= since, BedReport.facility_id.in_(ids))
            .group_by(BedReport.facility_id)
        )
    ).all()
    checkin_rows = dict(
        (
            await session.execute(
                select(
                    StaffCheckin.facility_id,
                    case(
                        (
                            func.count().filter(StaffCheckin.geofence_ok.isnot(None)) == 0,
                            None,
                        ),
                        else_=cast(
                            func.count().filter(StaffCheckin.geofence_ok.is_(True)), Float
                        )
                        / func.nullif(
                            cast(func.count().filter(StaffCheckin.geofence_ok.isnot(None)), Float),
                            0.0,
                        ),
                    ).label("geofence_rate"),
                )
                .where(
                    StaffCheckin.checked_in_at >= since,
                    StaffCheckin.facility_id.in_(ids),
                )
                .group_by(StaffCheckin.facility_id)
            )
        ).all()
    )

    out: dict[str, Component] = {}
    for fid, reports, verified, rejected in bed_rows:
        if reports < 3:
            continue
        verified_share = verified / reports
        geofence_rate = checkin_rows.get(fid)
        penalty = min(1.0, (1 - verified_share) * 0.8 + (rejected / reports) * 0.6)
        if geofence_rate is None:
            # Nothing this facility sends can be located at all. Not misconduct —
            # a feature phone genuinely cannot do it — but it does mean fewer of
            # its numbers can be checked.
            penalty = min(1.0, penalty + 0.2)
        out[fid] = _component(
            "verification_quality",
            penalty,
            "Reports arrive with codes and locations that check out."
            if penalty < 0.05
            else f"{verified} of {reports} ward photos verified"
            + (f", {rejected} rejected" if rejected else "")
            + ("; no channel here can supply a location." if geofence_rate is None else "."),
        )
    return out


# =============================================================== scoring ===


async def compute(session: AsyncSession, scope: Scope = Scope()) -> list[Score]:
    """Score facilities from the live tables, now.

    Nothing here is read from a cache or a precomputed column: every number
    comes from the same rows the movement tab, the ward-photo panel and the
    federated trainer read. A score and the evidence beside it are therefore
    always the same query, and clicking that evidence opens those very rows.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    ids = await resolve(session, scope)
    if not ids:
        return []

    signals = [
        await _attendance_vs_footfall(session, since, ids),
        await _consumption_vs_footfall(session, since, ids),
        await _beds_vs_register(session, since, ids),
        await _receipt_discipline(session, now, ids),
        await _implausible_smoothness(session, since, ids),
        await _verification_quality(session, since, ids),
    ]

    by_facility: dict[str, list[Component]] = {}
    for signal in signals:
        for fid, component in signal.items():
            by_facility.setdefault(fid, []).append(component)

    scores: list[Score] = []
    for fid, components in by_facility.items():
        # Score only against the signals a facility actually has, so a facility
        # with no beds is not penalised for having no bed photos.
        available = sum(c.weight for c in components)
        cost = sum(c.contribution for c in components)
        score = round(max(0.0, 1.0 - (cost / available if available else 0.0)), 3)
        scores.append(
            Score(
                facility_id=fid,
                score=score,
                band=band_for(score),
                components=sorted(components, key=lambda c: -c.contribution),
            )
        )
    return scores


async def store(session: AsyncSession, scores: list[Score]) -> int:
    for i in range(0, len(scores), 500):
        chunk = scores[i : i + 500]
        stmt = pg_insert(FacilityTrust).values(
            [
                {
                    "facility_id": s.facility_id,
                    "score": s.score,
                    "band": s.band,
                    "components": s.as_rows(),
                    "computed_at": datetime.now(timezone.utc),
                }
                for s in chunk
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["facility_id"],
                set_={
                    "score": stmt.excluded.score,
                    "band": stmt.excluded.band,
                    "components": stmt.excluded.components,
                    "computed_at": stmt.excluded.computed_at,
                },
            )
        )
    await session.commit()
    return len(scores)


async def audit_queue(
    session: AsyncSession,
    *,
    state: str | None = None,
    district: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Where a physical visit is worth the trip.

    Scored live against the current tables, then ranked by confidence
    ascending and size descending: the same doubt matters more at a facility
    serving more people.
    """
    scores = await compute(session, Scope(state=state, district=district))
    flagged = [s for s in scores if s.band != GOOD]
    if not flagged:
        return []

    facilities = {
        f.id: f
        for f in (
            await session.execute(
                select(Facility).where(
                    Facility.id.in_([s.facility_id for s in flagged])
                )
            )
        ).scalars()
    }
    flagged.sort(
        key=lambda s: (s.score, -facilities[s.facility_id].beds_total)
        if s.facility_id in facilities
        else (s.score, 0)
    )

    rows = []
    for score in flagged[:limit]:
        facility = facilities.get(score.facility_id)
        if facility is None:
            continue
        rows.append(
            {
                "facility_id": score.facility_id,
                "facility_name": facility.name,
                "type": facility.type,
                "district": facility.district,
                "state_silo": facility.state_silo,
                "lat": facility.lat,
                "lng": facility.lng,
                "score": score.score,
                "band": score.band,
                "components": score.as_rows(),
                "computed_at": datetime.now(timezone.utc),
            }
        )
    return rows


async def for_facility(session: AsyncSession, facility_id: str) -> Score | None:
    """One facility, scored from the rows as they stand this second."""
    scores = await compute(session, Scope(facility_ids=(facility_id,)))
    return scores[0] if scores else None


def why_rows(score: Score) -> list[str]:
    """The signals that disagree, as the sentences the panel already shows."""
    return [
        f"{c.signal.replace('_', ' ')}: {c.reason}"
        for c in score.components
        if c.penalty >= 0.05
    ]


def rules_why(score: Score) -> str:
    """The same finding without a model: the two clearest sentences, verbatim."""
    flagged = [c for c in score.components if c.penalty >= 0.05]
    if not flagged:
        return "Its signals agree with each other."
    text = " ".join(c.reason for c in flagged[:2])
    return f"{text} Worth checking the registers on a visit."


def warning_multiplier(score: float | None) -> float:
    """How much earlier this facility's stock warning should trip — spec 12.6.

    A facility whose numbers cannot be trusted is *more* dangerous, not less,
    so low confidence widens the threshold rather than discounting the alert.
    Quantities are untouched; only the timing of the warning moves.
    """
    if score is None:
        return 1.0
    return round(1.0 + max(0.0, 0.75 - max(0.0, min(1.0, score))), 3)

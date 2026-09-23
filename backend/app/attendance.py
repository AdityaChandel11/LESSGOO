"""Staff attendance — spec 26.1.

Biometric readers across 1.6 lakh facilities is a procurement fantasy, and a
reader that breaks takes a facility's attendance offline entirely. Location
does the same job with hardware that already exists, provided we are exact
about what each channel can actually supply:

    smartphone      browser GPS, metres          geofenced at 250 m
    USSD / gateway  serving cell tower, km       geofenced at 2 km
    IVR / SMS       the caller's number, nothing more — no geofence
    demo data       synthetic towers, labelled `simulated`

An inbound Twilio call does not carry the serving tower. That needs an
operator USSD gateway or a native handset app, so `loc_method` records what
actually produced the fix and a check that could not run is never displayed as
one that passed.

Attendance is compared against OPD footfall for the same day. Staff recorded
present with no patients logged is a question worth asking, never proof of
anything, and it attaches to a facility and a pattern — never to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .beds import distance_km, geofence_radius_km
from .models import Facility, StaffCheckin, StaffVerification, StockReading

# A staff member seen at any point in this window counts towards the roster.
# There is no establishment table to read: the roster is who actually works
# here, as evidenced by their own check-ins.
ROSTER_WINDOW_DAYS = 30
PRESENT_WINDOW_HOURS = 24
SHIFTS = ("morning", "evening", "night")


@dataclass(frozen=True)
class Attendance:
    facility_id: str
    roster: int
    present: int
    rate: float | None
    # How the present staff were located, so the UI can show what was verified
    # rather than implying all of it was.
    by_method: dict[str, int]
    geofence_pass: int
    geofence_checked: int
    footfall_today: int | None
    # Set when people are recorded present and no patients were logged.
    contradiction: str | None


def resolve_location(
    facility: Facility,
    *,
    lat: float | None,
    lng: float | None,
    loc_method: str,
) -> tuple[float | None, bool | None]:
    """Distance from the facility, and whether that clears the geofence.

    Returns (None, None) when no geofence could be run — the honest answer for
    a channel that carries no location at all.
    """
    radius = geofence_radius_km(loc_method)
    if radius is None or lat is None or lng is None:
        return None, None
    km = distance_km(lat, lng, facility.lat, facility.lng)
    return round(km, 3), km <= radius


async def footfall_for(
    session: AsyncSession, facility_id: str, day: datetime
) -> int | None:
    """OPD patients logged at this facility on this day.

    Footfall rides along on stock readings rather than living in its own table,
    so the highest figure reported for the day is the facility's own count.
    """
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return await session.scalar(
        select(func.max(StockReading.footfall_same_day)).where(
            StockReading.facility_id == facility_id,
            StockReading.reported_at >= start,
            StockReading.reported_at < start + timedelta(days=1),
        )
    )


async def record_checkin(
    session: AsyncSession,
    facility: Facility,
    *,
    staff_ref: str,
    action: str,
    shift: str | None,
    source: str,
    lat: float | None,
    lng: float | None,
    loc_method: str,
    cell_id: str | None,
    at: datetime | None = None,
) -> StaffCheckin:
    """Record a shift start or end with the evidence behind its location."""
    when = at or datetime.now(timezone.utc)
    km, ok = resolve_location(facility, lat=lat, lng=lng, loc_method=loc_method)
    footfall = await footfall_for(session, facility.id, when)

    if action == "out":
        # Close this person's most recent open shift rather than opening a row
        # that pretends a check-out happened without a check-in.
        open_row = await session.scalar(
            select(StaffCheckin)
            .where(
                StaffCheckin.facility_id == facility.id,
                StaffCheckin.staff_ref == staff_ref,
                StaffCheckin.checked_out_at.is_(None),
            )
            .order_by(StaffCheckin.checked_in_at.desc())
            .limit(1)
        )
        if open_row is not None:
            open_row.checked_out_at = when
            await session.flush()
            await _publish(session, facility, open_row, action="out")
            return open_row

    row = StaffCheckin(
        facility_id=facility.id,
        staff_ref=staff_ref,
        checked_in_at=when,
        shift=shift,
        source=source,
        cell_id=cell_id,
        loc_method=loc_method,
        geofence_km=km,
        geofence_ok=ok,
        footfall_same_period=footfall,
    )
    session.add(row)
    await session.flush()
    await _publish(session, facility, row, action="in")
    return row


async def _publish(
    session: AsyncSession, facility: Facility, row: StaffCheckin, *, action: str
) -> None:
    await events.record(
        session,
        events.STAFF_CHECKIN,
        {
            "facility_id": facility.id,
            "facility_name": facility.name,
            "action": action,
            "shift": row.shift,
            "source": row.source,
            "loc_method": row.loc_method,
            "geofence_ok": row.geofence_ok,
            # Never the staff reference: attendance is reported as a facility
            # pattern, never as a named individual (rule 8).
        },
        state_silo=facility.state_silo,
    )


async def summarise(session: AsyncSession, facility_id: str) -> Attendance:
    now = datetime.now(timezone.utc)
    roster_cutoff = now - timedelta(days=ROSTER_WINDOW_DAYS)
    present_cutoff = now - timedelta(hours=PRESENT_WINDOW_HOURS)

    rows = (
        await session.execute(
            select(
                StaffCheckin.staff_ref,
                StaffCheckin.checked_in_at,
                StaffCheckin.loc_method,
                StaffCheckin.geofence_ok,
            ).where(
                StaffCheckin.facility_id == facility_id,
                StaffCheckin.checked_in_at >= roster_cutoff,
            )
        )
    ).all()

    roster: set[str] = set()
    present: set[str] = set()
    by_method: dict[str, int] = {}
    geofence_pass = geofence_checked = 0
    for staff_ref, at, method, ok in rows:
        if not staff_ref:
            continue
        roster.add(staff_ref)
        if at >= present_cutoff:
            present.add(staff_ref)
            by_method[method or "none"] = by_method.get(method or "none", 0) + 1
            if ok is not None:
                geofence_checked += 1
                geofence_pass += int(bool(ok))

    footfall = await footfall_for(session, facility_id, now)
    contradiction = None
    if present and footfall == 0:
        contradiction = (
            f"{len(present)} staff recorded present today with no patients logged. "
            "Worth a second look — most often this is an unfilled register, not an absent team."
        )

    return Attendance(
        facility_id=facility_id,
        roster=len(roster),
        present=len(present),
        rate=round(len(present) / len(roster), 3) if roster else None,
        by_method=by_method,
        geofence_pass=geofence_pass,
        geofence_checked=geofence_checked,
        footfall_today=footfall,
        contradiction=contradiction,
    )


async def recent(
    session: AsyncSession, facility_id: str, limit: int = 12
) -> list[StaffCheckin]:
    return list(
        (
            await session.execute(
                select(StaffCheckin)
                .where(StaffCheckin.facility_id == facility_id)
                .order_by(StaffCheckin.checked_in_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


# ------------------------------------------------- one person's own record ---
# Everything above this line reports a facility. Everything below reports one
# person to themselves, and to nobody else.
#
# Rule 8 (v3 1.8) says attendance flags attach to a facility and a pattern,
# never a named individual. It is a rule about exposure: it exists so that a
# district officer cannot open a console and read a named health worker's
# movements. It was never a rule that a worker may not see their own record —
# refusing them that would mean the one person with a right to the data is the
# only one who cannot check it, and cannot correct it when it is wrong.
#
# So the boundary is enforced at the door: the endpoint reads `staff_ref` from
# the signed-in account and from nowhere else. There is no parameter to pass
# somebody else's, and the officer-facing `summarise()` above still returns
# counts with no reference in them at all.

# Long enough to show a month's pattern, short enough that the query stays
# bounded — the database size guard forbids unbounded reads.
SELF_WINDOW_DAYS = 30

# The outside edge of one shift, used to decide which shift a re-verification
# belongs to. Longer than a rostered eight hours, because a ping sent late in
# a shift that started late is still that shift's.
SHIFT_SPAN_HOURS = 14


@dataclass(frozen=True)
class VerificationPing:
    sent_at: datetime
    responded_at: datetime | None
    channel: str
    loc_method: str | None
    cell_id: str | None
    geofence_km: float | None
    geofence_ok: bool | None
    outcome: str


@dataclass(frozen=True)
class SelfDay:
    """One calendar day of one person's own record."""

    day: date
    present: bool
    checked_in_at: datetime | None
    checked_out_at: datetime | None
    shift: str | None
    source: str | None
    loc_method: str | None
    cell_id: str | None
    geofence_km: float | None
    geofence_ok: bool | None
    pings: list[VerificationPing]


@dataclass(frozen=True)
class SelfRecord:
    facility_id: str
    staff_ref: str
    window_days: int
    days_present: int
    # Working days in the window with no check-in at all. Counted, not judged:
    # leave, a posting elsewhere and a dead handset all land here, and the UI
    # says so.
    days_absent: int
    pings_sent: int
    pings_confirmed: int
    pings_unanswered: int
    days: list[SelfDay]


async def own_record(
    session: AsyncSession,
    facility_id: str,
    staff_ref: str,
    *,
    window_days: int = SELF_WINDOW_DAYS,
    now: datetime | None = None,
) -> SelfRecord:
    """This person's own attendance and re-verification history.

    Both queries are bounded by facility, staff reference and a date floor —
    never an open scan (see the size guard in CLAUDE.md).
    """
    at = now or datetime.now(timezone.utc)
    today = at.date()
    floor = (at - timedelta(days=window_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    checkins = list(
        (
            await session.execute(
                select(StaffCheckin)
                .where(
                    StaffCheckin.facility_id == facility_id,
                    StaffCheckin.staff_ref == staff_ref,
                    StaffCheckin.checked_in_at >= floor,
                )
                .order_by(StaffCheckin.checked_in_at.asc())
            )
        ).scalars()
    )
    pings = list(
        (
            await session.execute(
                select(StaffVerification)
                .where(
                    StaffVerification.facility_id == facility_id,
                    StaffVerification.staff_ref == staff_ref,
                    StaffVerification.sent_at >= floor,
                )
                .order_by(StaffVerification.sent_at.asc())
            )
        ).scalars()
    )

    # Keyed by date so a day with two check-ins keeps the first — the shift
    # that started the day — rather than whichever row the query returned last.
    by_day: dict[date, StaffCheckin] = {}
    for row in checkins:
        by_day.setdefault(row.checked_in_at.date(), row)

    # A ping is filed under the day of the shift it follows, not the day on
    # the clock when it was sent. A night shift starting at 20:00 is still
    # that day's shift when its re-verification arrives at half past one, and
    # a reader looking at Tuesday should find Tuesday night's ping on it.
    pings_by_day: dict[date, list[StaffVerification]] = {}
    starts = sorted((c.checked_in_at, c.checked_in_at.date()) for c in checkins)
    for p in pings:
        owner = p.sent_at.date()
        for started, day_of in reversed(starts):
            if started <= p.sent_at:
                # Beyond this and it is not the same shift any more, whatever
                # the clock says.
                if p.sent_at - started <= timedelta(hours=SHIFT_SPAN_HOURS):
                    owner = day_of
                break
        pings_by_day.setdefault(owner, []).append(p)

    days: list[SelfDay] = []
    for offset in range(window_days):
        d = today - timedelta(days=window_days - 1 - offset)
        row = by_day.get(d)
        days.append(
            SelfDay(
                day=d,
                present=row is not None,
                checked_in_at=row.checked_in_at if row else None,
                checked_out_at=row.checked_out_at if row else None,
                shift=row.shift if row else None,
                source=row.source if row else None,
                loc_method=row.loc_method if row else None,
                cell_id=row.cell_id if row else None,
                geofence_km=row.geofence_km if row else None,
                geofence_ok=row.geofence_ok if row else None,
                pings=[
                    VerificationPing(
                        sent_at=p.sent_at,
                        responded_at=p.responded_at,
                        channel=p.channel,
                        loc_method=p.loc_method,
                        cell_id=p.cell_id,
                        geofence_km=p.geofence_km,
                        geofence_ok=p.geofence_ok,
                        outcome=p.outcome,
                    )
                    for p in pings_by_day.get(d, [])
                ],
            )
        )

    # Newest first: the reader's own most recent shift is the thing they came
    # to check, and it should not be thirty rows down.
    days.reverse()
    present_count = sum(1 for d in days if d.present)

    return SelfRecord(
        facility_id=facility_id,
        staff_ref=staff_ref,
        window_days=window_days,
        days_present=present_count,
        days_absent=window_days - present_count,
        pings_sent=len(pings),
        pings_confirmed=sum(1 for p in pings if p.outcome == "confirmed"),
        pings_unanswered=sum(1 for p in pings if p.outcome == "no_reply"),
        days=days,
    )

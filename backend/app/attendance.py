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
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .beds import distance_km, geofence_radius_km
from .models import Facility, StaffCheckin, StockReading

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

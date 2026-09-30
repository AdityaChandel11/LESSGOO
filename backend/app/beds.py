"""Bed occupancy as a verified observation — spec 26.2.

Three independent checks run on every report, and each result is stored next to
the number it qualifies:

  the code   was this photo taken today?     (the day's rotating code, read
                                              back out of the image)
  the place  was it taken here?              (geofence against the facility's
                                              registered coordinates)
  the paper  does it match the register?     (admissions logged the same day)

A report that fails is stored, not dropped. Silently discarding a facility's
work teaches staff the system is broken, and a rejected report is itself a
signal — it is what `verification_quality` in the trust score counts.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .config import settings
from .models import BedReport, BedStatus, Facility, TrustFlag, VerificationCode
from .vision import BedExtraction

# No O/0, I/1 or S/5: the code is written on a whiteboard by one person and
# read off a photograph by another, so ambiguous glyphs cost more than length.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"
CODE_LENGTH = 4

VERIFIED = "verified"
UNVERIFIED = "unverified"
REJECTED = "rejected"

EARTH_RADIUS_KM = 6371.0088

# What scripts/seed.py writes into a seeded report's notes. A seeded row was
# never a photograph, so it is labelled as seeded rather than "read by mock".
SEED_NOTE = "Seeded ward photo extraction."

# How far back a centre's latest verified count is looked for. Bounds the
# query by date as well as by facility; anything older is long stale anyway.
LOOKBACK_DAYS = 30


def generate_code(rng: secrets.SystemRandom | None = None) -> str:
    picker = rng or secrets.SystemRandom()
    return "".join(picker.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


async def code_for(
    session: AsyncSession, facility_id: str, for_date: date | None = None
) -> VerificationCode:
    """Today's code for a facility, issued once and then stable.

    Concurrent requests race here — a facility opening the app on two phones —
    so the insert resolves the conflict in the database rather than in Python.
    """
    day = for_date or datetime.now(timezone.utc).date()
    existing = await session.get(VerificationCode, (facility_id, day))
    if existing is not None:
        return existing

    stmt = (
        pg_insert(VerificationCode)
        .values(facility_id=facility_id, for_date=day, code=generate_code())
        .on_conflict_do_nothing(index_elements=["facility_id", "for_date"])
    )
    await session.execute(stmt)
    await session.flush()
    issued = await session.scalar(
        select(VerificationCode).where(
            VerificationCode.facility_id == facility_id,
            VerificationCode.for_date == day,
        )
    )
    return issued


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlng = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def geofence_radius_km(loc_method: str | None) -> float | None:
    """How far off a location may be before it stops meaning anything.

    None where no geofence can be run: an inbound call gives us the caller,
    not the tower (spec 26.1), and pretending otherwise would be the one lie
    this layer cannot afford.
    """
    if loc_method == "gps":
        return settings.geofence_gps_km
    if loc_method in ("cell_id", "simulated"):
        return settings.geofence_cell_km
    return None


@dataclass
class Checks:
    """What each check concluded, and the sentence an officer reads."""

    code_ok: bool | None = None
    geofence_ok: bool | None = None
    geofence_km: float | None = None
    register_ok: bool | None = None
    verification: str = UNVERIFIED
    reasons: list[str] = field(default_factory=list)


def verify(
    extraction: BedExtraction,
    *,
    expected_code: str,
    loc_method: str | None,
    distance: float | None,
    register_admissions: int | None,
    registered_total: int | None = None,
) -> Checks:
    """Apply the three checks. Rejection needs evidence, not just absence of it.

    `registered_total` is the centre's registered bed capacity. A report that
    shows a different ward size, or more occupied beds than exist, contradicts
    a registered fact and is rejected with the reason (fix #68).
    """
    checks = Checks()

    # --- the capacity: does the count fit the beds this centre has? ---
    over_capacity = False
    if registered_total is not None:
        if extraction.beds_total is not None and extraction.beds_total != registered_total:
            over_capacity = True
            checks.reasons.append(
                f"The report shows {extraction.beds_total} beds; this centre is registered "
                f"for {registered_total}. Not counted."
            )
        elif extraction.beds_occupied is not None and extraction.beds_occupied > registered_total:
            over_capacity = True
            checks.reasons.append(
                f"{extraction.beds_occupied} occupied is more than the {registered_total} "
                f"registered beds. Not counted."
            )

    # --- the code: was this taken today? ---
    if extraction.code_read is None:
        checks.reasons.append("The verification code was not legible in the photo.")
    else:
        checks.code_ok = extraction.code_read.upper() == expected_code.upper()
        if not checks.code_ok:
            checks.reasons.append(
                f"The code in the photo ({extraction.code_read}) is not today's code "
                f"({expected_code}) — the photo is not from today."
            )

    # --- the place: was it taken here? ---
    radius = geofence_radius_km(loc_method)
    checks.geofence_km = round(distance, 3) if distance is not None else None
    if radius is None or distance is None:
        checks.reasons.append(
            "This channel carries no location, so where the photo was taken could not be checked."
            if loc_method in (None, "none")
            else "No location was supplied with the photo."
        )
    else:
        checks.geofence_ok = distance <= radius
        if not checks.geofence_ok:
            checks.reasons.append(
                f"Taken {distance:.1f} km from the facility's registered location, "
                f"outside the {radius:.2f} km limit for {loc_method.replace('_', ' ')}."
            )

    # --- the paper: does it match the register? ---
    if register_admissions is not None and extraction.beds_occupied is not None:
        if register_admissions == 0 and extraction.beds_occupied == 0:
            checks.register_ok = True
        else:
            base = max(register_admissions, 1)
            gap = abs(extraction.beds_occupied - register_admissions) / base
            checks.register_ok = gap <= settings.bed_register_tolerance
            if not checks.register_ok:
                checks.reasons.append(
                    f"{extraction.beds_occupied} beds occupied in the photo against "
                    f"{register_admissions} admissions in the register."
                )

    if extraction.beds_occupied is None:
        checks.reasons.append("The beds could not be counted from this photo.")
    elif extraction.confidence < settings.bed_confidence_floor:
        checks.reasons.append(
            f"Low confidence in the count ({extraction.confidence:.0%})."
        )

    # --- the verdict ---
    # Only a contradiction rejects: a code that says another day, or a precise
    # location that says another place. A missing check leaves the report
    # unverified, which is a request for a better photo, not an accusation.
    contradicted = over_capacity or checks.code_ok is False or (
        checks.geofence_ok is False and loc_method == "gps"
    )
    # "Verified" has to mean both halves: from today, and from here. A channel
    # that carries no location can never reach it — that is a true limit of a
    # feature phone, and the report says so rather than quietly passing.
    complete = (
        checks.code_ok is True
        and checks.geofence_ok is True
        and extraction.beds_occupied is not None
        and extraction.confidence >= settings.bed_confidence_floor
    )
    if contradicted:
        checks.verification = REJECTED
    elif complete:
        checks.verification = VERIFIED
    else:
        checks.verification = UNVERIFIED
    return checks


def hash_media(image: bytes) -> str:
    """A fingerprint of the photo, so the same image resubmitted is recognisable
    without storing the photograph itself."""
    return "sha256:" + hashlib.sha256(image).hexdigest()[:32]


async def record_report(
    session: AsyncSession,
    facility: Facility,
    extraction: BedExtraction,
    *,
    ward: str,
    source: str,
    loc_method: str | None,
    loc_lat: float | None,
    loc_lng: float | None,
    loc_accuracy_m: float | None,
    register_admissions: int | None,
    media_ref: str | None,
    reported_at: datetime | None = None,
) -> tuple[BedReport, Checks]:
    """Run the checks, store the report with its evidence, and publish it."""
    code_row = await code_for(session, facility.id)
    distance = (
        distance_km(loc_lat, loc_lng, facility.lat, facility.lng)
        if loc_lat is not None and loc_lng is not None
        else None
    )
    checks = verify(
        extraction,
        expected_code=code_row.code,
        loc_method=loc_method,
        distance=distance,
        register_admissions=register_admissions,
        registered_total=facility.beds_total,
    )

    report = BedReport(
        facility_id=facility.id,
        ward=ward,
        beds_total=extraction.beds_total,
        beds_occupied=extraction.beds_occupied,
        reported_at=reported_at or datetime.now(timezone.utc),
        source=source,
        code_expected=code_row.code,
        code_read=extraction.code_read,
        code_ok=checks.code_ok,
        loc_method=loc_method,
        loc_lat=loc_lat,
        loc_lng=loc_lng,
        loc_accuracy_m=loc_accuracy_m,
        geofence_km=checks.geofence_km,
        geofence_ok=checks.geofence_ok,
        register_admissions=register_admissions,
        model_confidence=extraction.confidence,
        media_ref=media_ref,
        raw_payload={
            "model": extraction.model,
            "notes": extraction.notes,
            "reasons": checks.reasons,
        },
        verification=checks.verification,
    )
    session.add(report)

    # Only a verified count is allowed to become the facility's stated
    # occupancy. An unverified one stays visible as a report awaiting a better
    # photo, which is the honest thing for the dashboard to show.
    if checks.verification == VERIFIED and extraction.beds_occupied is not None:
        session.add(
            BedStatus(
                facility_id=facility.id,
                beds_occupied=extraction.beds_occupied,
                recorded_at=report.reported_at,
            )
        )

    if checks.code_ok is False:
        session.add(
            TrustFlag(
                facility_id=facility.id,
                rule="stale_bed_photo",
                reason=checks.reasons[0],
            )
        )
    elif checks.register_ok is False:
        session.add(
            TrustFlag(
                facility_id=facility.id,
                rule="bed_register_mismatch",
                reason=next(
                    (r for r in checks.reasons if "register" in r), "Bed count disputes the register."
                ),
            )
        )

    await session.flush()
    await events.record(
        session,
        events.BED_REPORTED,
        {
            "facility_id": facility.id,
            "facility_name": facility.name,
            "ward": ward,
            "beds_total": extraction.beds_total,
            "beds_occupied": extraction.beds_occupied,
            "verification": checks.verification,
            "source": source,
            "model": extraction.model,
        },
        state_silo=facility.state_silo,
    )
    return report, checks


async def recent_reports(
    session: AsyncSession, facility_id: str, limit: int = 10
) -> list[BedReport]:
    return list(
        (
            await session.execute(
                select(BedReport)
                .where(BedReport.facility_id == facility_id)
                .order_by(BedReport.reported_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


@dataclass(frozen=True)
class BedFigure:
    """A centre's one bed figure: its latest verified count, and when."""

    occupied: int
    as_of: datetime
    stale: bool


def is_stale(as_of: datetime, now: datetime) -> bool:
    """Older than the window, a verified count is not counted as available."""
    return now - as_of > timedelta(hours=settings.bed_stale_hours)


async def latest_verified(
    session: AsyncSession, ids: list[str], now: datetime
) -> dict[str, BedFigure]:
    """Each centre's latest verified ward count — the only bed figure any view
    shows (fix #68). Bounded by facility and by date."""
    rows = await session.execute(
        select(BedReport.facility_id, BedReport.beds_occupied, BedReport.reported_at)
        .where(
            BedReport.facility_id.in_(ids),
            BedReport.verification == VERIFIED,
            BedReport.beds_occupied.is_not(None),
            BedReport.reported_at >= now - timedelta(days=LOOKBACK_DAYS),
        )
        .order_by(BedReport.facility_id, BedReport.reported_at.desc())
    )
    figures: dict[str, BedFigure] = {}
    for fid, occupied, at in rows.all():
        if fid not in figures:
            figures[fid] = BedFigure(occupied=occupied, as_of=at, stale=is_stale(at, now))
    return figures


CHANNEL_WORDS = {"sms": "SMS", "ivr": "IVR", "whatsapp": "WhatsApp", "voice": "a voice call"}


def read_by(*, model: str | None, notes: str | None, source: str) -> str:
    """Who produced a report's numbers, in words a judge will not misread."""
    if notes == SEED_NOTE:
        return "Seeded demonstration report (no photo)"
    if model == "typed":
        return f"Typed over {CHANNEL_WORDS.get(source, source)} (no photo)"
    if model == "mock":
        return "Test extractor — no model was called"
    return f"Read by {model}" if model else f"Reported over {source}"

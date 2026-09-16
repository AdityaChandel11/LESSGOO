"""Outbreak pre-positioning — spec 12.5.

Deliberately not a new subsystem. A declared outbreak raises expected demand
for the commodities that particular disease actually consumes, inside a radius,
for a fixed window. Everything downstream reacts on its own: days of cover
fall, the map turns, and the existing solver proposes transfers toward the
affected area — before a single facility has reported a shortage.

That is the whole argument. The reactive system moves stock after a facility
reports it has run out, which is days after the patients arrived. This moves it
when the outbreak is declared. Same solver, same safety rules, same human
approval on every transfer.

The commodity table below is a clinical judgement, not a model output. It is
short, readable, and meant to be argued with by someone who treats these
diseases — which is exactly why it is a table in source control rather than a
weight inside a network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .models import Facility, OutbreakDemand, OutbreakEvent, Sku

# Clinician-reviewable, versioned with the code. Each number is "how much more
# of this commodity a facility gets through, per patient, during this disease".
OUTBREAK_COMMODITY_MAP: dict[str, dict[str, float]] = {
    "acute_diarrheal_disease": {"ORS": 3.0, "ZINC": 2.5, "IVFLUID": 2.0},
    "dengue_suspected": {"IVFLUID": 2.5, "PARA500": 1.5},
    "heatstroke": {"IVFLUID": 3.0, "ORS": 2.0},
    "cholera_suspected": {"ORS": 3.5, "IVFLUID": 2.5, "ZINC": 2.0},
    "malaria_cluster": {"ACT": 3.0, "PARA500": 1.5},
}

CATEGORY_LABELS = {
    "acute_diarrheal_disease": "Acute diarrhoeal disease",
    "dengue_suspected": "Dengue, suspected",
    "heatstroke": "Heatstroke",
    "cholera_suspected": "Cholera, suspected",
    "malaria_cluster": "Malaria cluster",
}

DEFAULT_TTL_DAYS = 14
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Declaration:
    category: str
    district: str
    state_silo: str
    lat: float
    lng: float
    radius_km: float
    severity: float  # 0.0 (nothing) .. 1.0 (the full commodity multiplier)
    ttl_days: float = DEFAULT_TTL_DAYS
    note: str | None = None


def multiplier_for(category: str, sku: str, severity: float) -> float:
    """How much more of this medicine to expect, at this severity.

    Severity scales between normal and the full clinical multiplier, so a
    watchful declaration and a full-blown epidemic are the same mechanism at
    different strengths rather than two different code paths.
    """
    base = OUTBREAK_COMMODITY_MAP.get(category, {}).get(sku)
    if base is None:
        return 1.0
    return 1.0 + max(0.0, min(1.0, severity)) * (base - 1.0)


def _haversine_sql(lat: float, lng: float):
    """Great-circle distance in SQL. No PostGIS in this deployment (models.py
    records why), and at this scale a haversine expression is equivalent."""
    return EARTH_RADIUS_KM * func.acos(
        func.least(
            1.0,
            func.cos(func.radians(lat))
            * func.cos(func.radians(Facility.lat))
            * func.cos(func.radians(Facility.lng) - func.radians(lng))
            + func.sin(func.radians(lat)) * func.sin(func.radians(Facility.lat)),
        )
    )


async def facilities_within(
    session: AsyncSession, lat: float, lng: float, radius_km: float, state: str | None = None
) -> list[Facility]:
    stmt = select(Facility).where(_haversine_sql(lat, lng) <= radius_km)
    if state:
        stmt = stmt.where(Facility.state_silo == state)
    return list((await session.execute(stmt)).scalars())


async def declare(
    session: AsyncSession, declaration: Declaration, *, actor: str
) -> tuple[OutbreakEvent, list[str], list[str]]:
    """Record the outbreak and raise demand inside the radius.

    Returns the event, the commodities it raised, and the facilities it
    covers — the caller refreshes those facilities' map snapshots, because the
    national map reads a denormalised table and would otherwise keep showing
    yesterday's colours over a district that is now short.
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=declaration.ttl_days)

    affected = await facilities_within(
        session,
        declaration.lat,
        declaration.lng,
        declaration.radius_km,
        declaration.state_silo,
    )
    commodities = OUTBREAK_COMMODITY_MAP.get(declaration.category, {})
    known = {
        code
        for code in (await session.execute(select(Sku.code))).scalars()
        if code in commodities
    }

    event = OutbreakEvent(
        district=declaration.district,
        state_silo=declaration.state_silo,
        disease_category=declaration.category,
        radius_km=declaration.radius_km,
        severity=declaration.severity,
        lat=declaration.lat,
        lng=declaration.lng,
        facilities_affected=len(affected),
        declared_by=actor,
        note=declaration.note,
        expires_at=expires,
    )
    session.add(event)
    await session.flush()

    session.add_all(
        [
            OutbreakDemand(
                outbreak_id=event.id,
                facility_id=facility.id,
                sku_code=code,
                multiplier=multiplier_for(declaration.category, code, declaration.severity),
                expires_at=expires,
            )
            for facility in affected
            for code in sorted(known)
        ]
    )
    await session.flush()

    await events.record(
        session,
        events.OUTBREAK_SIMULATED,
        {
            "outbreak_id": event.id,
            "district": declaration.district,
            "category": declaration.category,
            "label": CATEGORY_LABELS.get(declaration.category, declaration.category),
            "radius_km": declaration.radius_km,
            "severity": declaration.severity,
            "facilities": len(affected),
            "skus": sorted(known),
        },
        state_silo=declaration.state_silo,
    )
    return event, sorted(known), [f.id for f in affected]


async def clear(session: AsyncSession, outbreak_id: int) -> list[str]:
    """Withdraw a declaration. Demand returns to normal immediately.

    Returns the facilities that were affected, so their map snapshots can be
    recomputed the same way declaring did.
    """
    event = await session.get(OutbreakEvent, outbreak_id)
    if event is None or event.cleared_at is not None:
        return []
    affected = list(
        (
            await session.execute(
                select(OutbreakDemand.facility_id)
                .where(OutbreakDemand.outbreak_id == outbreak_id)
                .distinct()
            )
        ).scalars()
    )
    event.cleared_at = datetime.now(timezone.utc)
    await session.execute(
        delete(OutbreakDemand).where(OutbreakDemand.outbreak_id == outbreak_id)
    )
    await session.flush()
    return affected


async def active_multipliers(
    session: AsyncSession, facility_ids: list[str]
) -> dict[tuple[str, str], float]:
    """Live demand multipliers for these facilities.

    Expiry is by the clock, not by a sweep: an outbreak that has run its course
    stops affecting anything the moment it does, whether or not a cleanup job
    ran.
    """
    if not facility_ids:
        return {}
    rows = (
        await session.execute(
            select(
                OutbreakDemand.facility_id,
                OutbreakDemand.sku_code,
                func.max(OutbreakDemand.multiplier),
            )
            .where(
                OutbreakDemand.facility_id.in_(facility_ids),
                OutbreakDemand.expires_at > datetime.now(timezone.utc),
            )
            .group_by(OutbreakDemand.facility_id, OutbreakDemand.sku_code)
        )
    ).all()
    return {(f, s): float(m) for f, s, m in rows}


async def active(session: AsyncSession, state: str | None = None) -> list[OutbreakEvent]:
    stmt = select(OutbreakEvent).where(
        OutbreakEvent.cleared_at.is_(None),
        OutbreakEvent.expires_at > datetime.now(timezone.utc),
    )
    if state:
        stmt = stmt.where(OutbreakEvent.state_silo == state)
    return list((await session.execute(stmt.order_by(OutbreakEvent.triggered_at.desc()))).scalars())


def warning_gained(
    days_before: float | None, days_after: float | None, ttl_days: float = DEFAULT_TTL_DAYS
) -> float | None:
    """How much earlier this facility is now visible as at risk.

    The honest measure of pre-positioning: not "we predicted an outbreak", but
    "under this declared demand, this facility's cover falls below a week N
    days sooner than the old rate suggested, and a transfer can start today
    instead of after it reports a shortage."
    """
    if days_before is None or days_after is None:
        return None
    return round(max(0.0, min(days_before, ttl_days * 2) - days_after), 1)

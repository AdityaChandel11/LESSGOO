"""HTTP API — spec Section 11.

Spec Section 1, rule 3 restated as it actually holds now that the capture
layer exists: this module may reach an external service ONLY through that
service's mode-switched adapter — `vision` (LLM_MODE), `maps` (MAPS_MODE, via
`redistribution`), and later `comms` (COMMS_MODE). Every one of those defaults
to a local path, so the whole API still runs, and every endpoint here still
answers, with a blank `.env`. A direct `httpx` call to a vendor, or a key read
straight from settings in this file, breaks that guarantee — those belong in
the adapter, where the fallback and the tests live.
"""

import asyncio
import base64
import binascii
import hashlib
import math
from uuid import uuid4
from dataclasses import asdict
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import (
    ingest,
    aggregates,
    attendance,
    beds,
    earlywarning,
    events,
    federation_live,
    geo,
    idsp,
    movements,
    ncdc,
    outbreak,
    redistribution,
    services,
    comms,
    stockphoto,
    trust,
    vision,
    workspace,
)
from .auth import (
    Principal,
    can_decide_transfer,
    can_plan_state,
    can_read_facility_rows,
    can_submit_reading,
    can_report_facts,
    can_view_facility,
    current_user,
    held_message,
    demo_may_plan,
    demo_may_write,
    demo_sandbox_refusal,
    in_demo_sandbox,
    is_public_demo,
    next_full_plan_at,
)
from .config import settings
from .db import SessionLocal, get_session, ping
from .models import (
    CALL_OUTCOMES,
    LOC_METHODS,
    Approval,
    CallLog,
    Event,
    Facility,
    FacilityContact,
    FederationRound,
    Forecast,
    IdspReport,
    MedicineMovement,
    OutbreakEvent,
    Sku,
    StockReading,
    Transfer,
)

WEB_SOURCES = frozenset({"form", "photo", "voice"})
DEMO_CHANNEL_SOURCES = frozenset({"sms", "ivr", "whatsapp"})

# Everything on `router` requires a signed-in user. `public_router` carries the
# short list that must answer without one: the health probe a load balancer
# calls, the runtime config the browser needs before it can render anything,
# and the two national aggregates the public landing page is built from. The
# line is drawn at detail, not at sensitivity — a state-level count of how many
# health centres are short of stock is the thing the page exists to say, while
# district rollups and individual facilities stay behind a session.
# test_api_boundary.py pins that list, so widening it is a deliberate edit.
router = APIRouter(dependencies=[Depends(current_user)])
public_router = APIRouter()

log = logging.getLogger(__name__)


class SkuOut(BaseModel):
    code: str
    name: str
    unit: str
    is_controlled: bool
    cold_chain: bool


class BucketOut(BaseModel):
    key: str
    label: str
    lat: float
    lng: float
    total: int
    critical: int
    at_risk: int
    healthy: int
    min_days: float | None
    critical_pct: float
    status: str
    zoom: int
    parent: str | None = None


class PinOut(BaseModel):
    id: str
    name: str
    type: str
    district: str
    state_silo: str
    lat: float
    lng: float
    status: str
    min_days: float | None
    critical_skus: int
    at_risk_skus: int


def _bucket_out(b: aggregates.Bucket) -> BucketOut:
    return BucketOut(
        key=b.key,
        label=b.label,
        lat=b.lat,
        lng=b.lng,
        total=b.total,
        critical=b.critical,
        at_risk=b.at_risk,
        healthy=b.healthy,
        min_days=b.min_days,
        critical_pct=b.critical_pct,
        status=b.status,
        zoom=b.zoom,
        parent=b.parent,
    )


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    demo_mode: bool
    database: bool
    modes: dict[str, str]
    external_services_in_use: bool
    # Whether the built website is being served from this same origin. A
    # deployment answering the API while serving no site looks healthy from
    # every other angle, so it is reported here rather than left to a log.
    site_served: bool
    # Why not, when not — a phrase, never the path, because this endpoint is
    # public. Distinguishes a wrong path from an empty copy from a bad build.
    site_detail: str | None = None
    # Only outside production: a filesystem path is a detail a public endpoint
    # has no reason to hand out, but it is the first thing you want locally.
    site_root: str | None = None


class SkuStockOut(BaseModel):
    sku_code: str
    sku_name: str
    qty_on_hand: float
    daily_burn_rate: float | None
    days_of_stock: float | None
    rate_source: str
    status: str
    is_controlled: bool
    cold_chain: bool
    last_reported_at: datetime | None
    last_source: str | None
    last_confidence: float | None
    forecast_published_at: datetime | None = None


class FacilityOut(BaseModel):
    id: str
    name: str
    type: str
    district: str
    state_silo: str
    lat: float
    lng: float
    status: str
    stock_status: str
    escalated: bool
    escalation_reasons: list[str]
    beds_total: int
    beds_occupied: int | None
    beds_verified_at: datetime | None
    beds_stale: bool
    bed_occupancy_pct: float | None
    staff_checkin_pct: float | None
    trust_score: float | None
    trust_band: str | None
    warning_multiplier: float
    critical_count: int
    at_risk_count: int
    tracked_skus: int


class FacilityDetailOut(FacilityOut):
    skus: list[SkuStockOut]


class StockReadingIn(BaseModel):
    facility_id: str
    sku_code: str
    qty_on_hand: float = Field(ge=0)
    source: str = "form"
    reported_at: datetime | None = None
    footfall_same_day: int | None = None
    reporter_ref: str | None = None
    channel_msg_id: str | None = None
    confidence: float | None = None


class StockReadingOut(BaseModel):
    id: int
    facility_id: str
    sku_code: str
    qty_on_hand: float
    days_of_stock: float | None
    status_before: str
    status_after: str
    status_changed: bool
    duplicate: bool = False


def _to_out(snap: services.FacilitySnapshot) -> FacilityOut:
    return FacilityOut(
        id=snap.id,
        name=snap.name,
        type=snap.type,
        district=snap.district,
        state_silo=snap.state_silo,
        lat=snap.lat,
        lng=snap.lng,
        status=snap.status,
        stock_status=snap.stock_status,
        escalated=snap.escalated,
        escalation_reasons=snap.escalation_reasons,
        beds_total=snap.beds_total,
        beds_occupied=snap.beds_occupied,
        beds_verified_at=snap.beds_verified_at,
        beds_stale=snap.beds_stale,
        bed_occupancy_pct=snap.bed_occupancy_pct,
        staff_checkin_pct=snap.staff_checkin_pct,
        trust_score=snap.trust_score,
        trust_band=snap.trust_band,
        warning_multiplier=snap.warning_multiplier,
        critical_count=sum(1 for s in snap.skus if s.status == services.STATUS_CRITICAL),
        at_risk_count=sum(1 for s in snap.skus if s.status == services.STATUS_AT_RISK),
        tracked_skus=len(snap.skus),
    )


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
class ClientConfigOut(BaseModel):
    maps_mode: str
    maps_browser_key: str
    demo_mode: bool
    # Which vision path the bed capture is on. The two are not interchangeable
    # from the browser's side: a live model needs a real image, and the
    # submit endpoint refuses a simulated extraction while one is configured.
    llm_mode: str


@public_router.get("/client-config", response_model=ClientConfigOut, tags=["meta"])
async def client_config() -> ClientConfigOut:
    """Settings the browser needs at runtime, so one built image serves every
    environment. Only values that are safe for any visitor to see."""
    google = settings.maps_mode == "google" and bool(settings.google_maps_browser_key)
    return ClientConfigOut(
        maps_mode="google" if google else "osm",
        maps_browser_key=settings.google_maps_browser_key if google else "",
        demo_mode=settings.demo_mode,
        llm_mode="live" if settings.llm_mode == "live" else "mock",
    )


@public_router.get("/health", response_model=HealthOut, tags=["meta"])
async def health() -> HealthOut:
    return HealthOut(
        status="ok",
        app=settings.app_name,
        version=settings.api_version,
        environment=settings.environment,
        demo_mode=settings.demo_mode,
        database=await ping(),
        modes={
            "llm": settings.llm_mode,
            "maps": settings.maps_mode,
            "comms": settings.comms_mode,
        },
        external_services_in_use=settings.uses_external_services,
        site_served=settings.serves_built_site,
        site_detail=settings.site_diagnosis,
        site_root=None if settings.is_production else str(settings.site_root),
    )


# --------------------------------------------------------------------------
# Map — level of detail by zoom (see aggregates.py)
# --------------------------------------------------------------------------
@router.get("/skus", response_model=list[SkuOut], tags=["map"])
async def list_skus(session: AsyncSession = Depends(get_session)) -> list[SkuOut]:
    rows = (await session.execute(select(Sku).order_by(Sku.name))).scalars().all()
    return [
        SkuOut(
            code=s.code,
            name=s.name,
            unit=s.unit,
            is_controlled=s.is_controlled,
            cold_chain=s.cold_chain,
        )
        for s in rows
    ]


@public_router.get("/map/summary", tags=["map"])
async def map_summary(
    sku: str | None = Query(default=None),
    state: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await aggregates.summary(session, sku, state)


@public_router.get("/map/states", response_model=list[BucketOut], tags=["map"])
async def map_states(
    sku: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[BucketOut]:
    return [_bucket_out(b) for b in await aggregates.state_rollup(session, sku)]


def _narrow_to_rows_scope(
    user: Principal, state: str | None, district: str | None
) -> tuple[str, str | None] | None:
    """The (state, district) filter a facility-level list is read with, or
    None when the caller may read no rows there (fix #77).

    A state officer is narrowed to their state, a district officer to their
    district; asking for somewhere else yields nothing rather than an error,
    because a map viewport crosses borders all the time. The national role
    and a centre's own account have no list of centres to read here.
    """
    if user.role == "state_officer" and user.state_silo:
        if state and state != user.state_silo:
            return None
        return user.state_silo, district
    if user.role == "block_mo" and user.state_silo and user.district:
        if (state and state != user.state_silo) or (district and district != user.district):
            return None
        return user.state_silo, user.district
    if user.role == "admin" and is_public_demo(user):
        # The labelled exception (auth.can_read_facility_rows): the sandbox
        # district, and nothing else.
        box = (settings.demo_sandbox_state, settings.demo_sandbox_district)
        if (state and state != box[0]) or (district and district != box[1]):
            return None
        return box
    return None


async def _readable_facility(session: AsyncSession, facility_id: str, user: Principal) -> Facility:
    """One centre, for a reader who may read its rows (fix #77). Anyone else
    is told where the rows are held; the centre's own name is not in the
    refusal."""
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    if not can_read_facility_rows(
        user, state=facility.state_silo, district=facility.district, facility_id=facility.id
    ):
        raise HTTPException(status_code=403, detail=held_message(user, facility.state_silo))
    return facility


@router.get("/map/districts", response_model=list[BucketOut], tags=["map"])
async def map_districts(
    state: str | None = Query(default=None),
    sku: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[BucketOut]:
    return [_bucket_out(b) for b in await aggregates.district_rollup(session, state, sku)]


@router.get("/map/facilities", response_model=list[PinOut], tags=["map"])
async def map_facilities(
    south: float | None = Query(default=None),
    west: float | None = Query(default=None),
    north: float | None = Query(default=None),
    east: float | None = Query(default=None),
    state: str | None = Query(default=None),
    district: str | None = Query(default=None),
    sku: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=1500, ge=1, le=4000),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[PinOut]:
    """Pins inside the caller's scope and nowhere else (fix #77): a viewport
    is narrowed to the state or district whose rows the caller may read, and
    the national role gets none — it reads /map/districts instead."""
    corners = (south, west, north, east)
    if any(c is not None for c in corners) and not all(c is not None for c in corners):
        raise HTTPException(status_code=422, detail="bbox needs south, west, north and east")
    bbox = corners if all(c is not None for c in corners) else None
    if bbox is None and state is None:
        raise HTTPException(status_code=422, detail="Provide a bbox or a state")
    narrowed = _narrow_to_rows_scope(user, state, district)
    if narrowed is None:
        return []
    state, district = narrowed
    pins = await aggregates.find_facilities(
        session,
        bbox=bbox,  # type: ignore[arg-type]
        state=state,
        district=district,
        sku=sku,
        status=status,
        limit=limit,
    )
    return [PinOut(**vars(p)) for p in pins]


# --------------------------------------------------------------------------
# Redistribution (spec 12.3) — plan, review, decide
# --------------------------------------------------------------------------
class FacilityRef(BaseModel):
    id: str
    name: str
    district: str
    lat: float
    lng: float


class TransferOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    status: str
    sku_code: str
    sku_name: str
    unit: str
    cold_chain: bool
    qty: float
    route_km: float
    eta_hours: float
    route_source: str | None
    triggered_by: str | None
    created_at: datetime
    rationale: dict
    from_: FacilityRef = Field(alias="from")
    to: FacilityRef


class ShortfallOut(BaseModel):
    facility_id: str
    name: str
    district: str
    sku_code: str
    sku_name: str
    days: float | None
    units_needed: int
    reason: str


class PlanIn(BaseModel):
    state: str
    sku: str | None = None


class PlanOut(BaseModel):
    state: str
    sku: str | None
    solver: str
    generated_at: datetime
    totals: dict
    transfers: list[TransferOut]
    unmet: list[ShortfallOut]
    manual_review: list[ShortfallOut]


async def _last_full_plan_at(
    session: AsyncSession, state: str, since: datetime
) -> datetime | None:
    """When the latest all-medicine plan for a state was computed, if that was
    after `since`. Every plan run writes one transfer.proposed event carrying
    its sku (null for all medicines), and events.created_at is indexed, so the
    time bound keeps this a short index range, never a scan."""
    return await session.scalar(
        select(func.max(Event.created_at)).where(
            Event.created_at >= since,
            Event.kind == events.TRANSFER_PROPOSED,
            Event.state_silo == state,
            Event.payload["sku"].astext.is_(None),
        )
    )


def _minutes(n: int) -> str:
    return "1 minute" if n == 1 else "{0} minutes".format(n)


async def _refuse_early_full_plan(session: AsyncSession, state: str) -> None:
    """429 when a public demo account asks for a second all-medicine plan of a
    state within settings.demo_plan_interval_minutes. A full plan deletes and
    rewrites the state's proposals and holds the free instance's CPU for
    seconds; a stranger pressing it in a loop is the whole site slowing down."""
    now = datetime.now(timezone.utc)
    interval = settings.demo_plan_interval_minutes
    last = await _last_full_plan_at(session, state, since=now - timedelta(minutes=interval))
    allowed_at = next_full_plan_at(last, now, interval)
    if allowed_at is None:
        return
    wait_s = (allowed_at - now).total_seconds()
    ago = int((now - last).total_seconds() // 60)
    raise HTTPException(
        status_code=429,
        detail=(
            "This state's full plan was recomputed {0} ago. The public demo allows one "
            "every {1}; try again in {2}. A single medicine can be planned any time."
        ).format(
            "less than a minute" if ago < 1 else _minutes(ago),
            _minutes(int(interval)),
            _minutes(math.ceil(wait_s / 60)),
        ),
        headers={"Retry-After": str(math.ceil(wait_s))},
    )


@router.post("/transfers/plan", response_model=PlanOut, tags=["transfers"])
async def plan_transfers(
    payload: PlanIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> PlanOut:
    if not can_plan_state(user, payload.state):
        raise HTTPException(
            status_code=403, detail="Only this state's officers can generate its transfer plan"
        )
    if not demo_may_plan(user, payload.state):
        raise HTTPException(status_code=403, detail=demo_sandbox_refusal())
    if payload.sku is None and is_public_demo(user):
        await _refuse_early_full_plan(session, payload.state)
    if await session.scalar(
        select(Facility.id).where(Facility.state_silo == payload.state).limit(1)
    ) is None:
        raise HTTPException(status_code=404, detail="Unknown state")
    if payload.sku and await session.get(Sku, payload.sku) is None:
        raise HTTPException(status_code=404, detail="Unknown SKU")

    result = await redistribution.generate_plan(session, payload.state, payload.sku)
    transfers = await redistribution.list_transfers(session, ids=result.transfer_ids)

    totals = {
        "transfers": len(transfers),
        "units": int(sum(t["qty"] for t in transfers)),
        "facilities_helped": len({t["to"]["id"] for t in transfers}),
        "donor_facilities": len({t["from"]["id"] for t in transfers}),
        "facilities_still_short": len({u["facility_id"] for u in result.unmet}),
        "total_km": round(sum(t["route_km"] for t in transfers), 1),
        "manual_review": len(result.manual_review),
    }
    await events.record(
        session,
        events.TRANSFER_PROPOSED,
        # `replaced` lets a donor acting on a removed proposal be told when the
        # plan replaced it (_replaced_at), instead of "not found".
        {"state": payload.state, "sku": payload.sku, **totals, "replaced": result.replaced_ids},
        state_silo=payload.state,
    )
    return PlanOut(
        state=result.state,
        sku=result.sku,
        solver=result.solver,
        generated_at=result.generated_at,
        totals=totals,
        transfers=[TransferOut.model_validate(t) for t in transfers],
        unmet=[ShortfallOut(**u) for u in result.unmet],
        manual_review=[ShortfallOut(**u) for u in result.manual_review],
    )


@router.get("/transfers/oversight", tags=["transfers"])
async def transfers_oversight(
    state: str = Query(..., min_length=2, max_length=4),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> dict:
    """Fix #39: one state's redistribution as an officer oversees it — the
    pipeline, what is stuck, and the exceptions that are theirs to act on.
    Bounded by the state's facilities and the last 30 days."""
    if await session.scalar(select(Facility.id).where(Facility.state_silo == state).limit(1)) is None:
        raise HTTPException(status_code=404, detail="Unknown state")
    out = await redistribution.oversight(session, state, datetime.now(timezone.utc))
    # Fix #77: the pipeline's counts are an aggregate anyone signed in may see;
    # the lists beneath it name centres, and are the state's officer's.
    if user.role == "state_officer" and user.state_silo == state:
        return {**out, "rows_withheld": None}
    # Every list in the reply names centres (stuck trips, unconfirmed
    # deliveries, declined, controlled, unreached); every count beside it stays.
    return {
        **{k: ([] if isinstance(v, list) else v) for k, v in out.items()},
        "rows_withheld": held_message(user, state),
    }


@router.get("/transfers", response_model=list[TransferOut], tags=["transfers"])
async def get_transfers(
    state: str | None = Query(default=None),
    status: str | None = Query(default=None, description="comma-separated"),
    limit: int = Query(default=300, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[TransferOut]:
    """Trips inside the caller's scope (fix #77): a state officer's own state,
    the trips that touch a district officer's district or a centre's own
    shelf. A trip names two centres, so the national role gets none here and
    reads the pipeline's counts from /transfers/oversight."""
    narrowed = _narrow_to_rows_scope(user, state, None)
    if narrowed is None and user.role != "facility_user":
        return []
    statuses = [s.strip() for s in status.split(",")] if status else None
    rows = await redistribution.list_transfers(
        session, state=narrowed[0] if narrowed else user.state_silo,
        statuses=statuses, limit=limit,
    )
    if user.role == "facility_user":
        rows = [r for r in rows if user.facility_id in (r["from"]["id"], r["to"]["id"])]
    elif narrowed[1]:
        rows = [r for r in rows if narrowed[1] in (r["from"]["district"], r["to"]["district"])]
    return [TransferOut.model_validate(r) for r in rows]


class ExplanationOut(BaseModel):
    """A plain-language "why" for something already on screen.

    `ai` is the only thing the screen may use to put a model's name on it. When
    the model is off, out of quota, or its answer failed a check, the fixed
    sentence comes back with `source="rules"` and `ai=False`.
    """

    text: str
    source: str  # "gemini" | "rules"
    ai: bool
    model: str | None
    latency_ms: int | None
    cached: bool
    note: str | None = None


def _rules_explanation(text: str, note: str | None = None) -> ExplanationOut:
    return ExplanationOut(
        text=text, source="rules", ai=False, model=None, latency_ms=None,
        cached=False, note=note,
    )


async def _explain_or_rules(make, fallback: str) -> ExplanationOut:
    """One model attempt, then the fixed sentence. POST, and only from a click:
    the free tier allows 20 generate requests a day per model."""
    if not vision.explanation_available():
        return _rules_explanation(fallback)
    try:
        answer = await make()
    except vision.VisionError as exc:
        return _rules_explanation(fallback, "Showing the computed line — {0}.".format(exc))
    return ExplanationOut(
        text=answer.text, source="gemini", ai=True, model=answer.model,
        latency_ms=answer.latency_ms, cached=answer.cached,
    )


class TripExplainIn(BaseModel):
    transfer_ids: list[int] = Field(min_length=1, max_length=20)


@router.post("/transfers/explain", response_model=ExplanationOut, tags=["transfers"])
async def explain_trip(
    body: TripExplainIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> ExplanationOut:
    """Why this trip, worded from the solver's own figures. The solver decided;
    this only says it in a sentence (spec 1.7)."""
    wanted = set(body.transfer_ids)
    items = await redistribution.list_transfers(session, ids=list(wanted))
    if len(items) != len(wanted):
        raise HTTPException(status_code=404, detail="Transfer not found")
    # A trip's figures are two centres' rows (fix #77).
    for t in items:
        state = t["state"]
        ends = (t["from"], t["to"])
        if not any(
            can_read_facility_rows(user, state=state, district=e["district"], facility_id=e["id"])
            for e in ends
        ):
            raise HTTPException(status_code=403, detail=held_message(user, state))
    if len({(t["from"]["id"], t["to"]["id"]) for t in items}) != 1:
        raise HTTPException(
            status_code=422, detail="Transfers on one trip share a donor and a receiver"
        )
    rows = redistribution.why_rows(items, settings.critical_days)
    return await _explain_or_rules(
        lambda: vision.explain_transfer(rows=rows), redistribution.rules_why(items)
    )


async def _replaced_at(session: AsyncSession, transfer_id: int) -> datetime | None:
    """When a re-plan removed this solver proposal, if one did in the last
    week. Each plan run's event lists the ids it replaced; the time bound and
    the kind keep this to a short index range over events.created_at."""
    return await session.scalar(
        text(
            """
            SELECT created_at FROM events
            WHERE created_at >= :since AND kind = :kind
              AND payload->'replaced' @> jsonb_build_array(CAST(:tid AS bigint))
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {
            "since": datetime.now(timezone.utc) - timedelta(days=7),
            "kind": events.TRANSFER_PROPOSED,
            "tid": transfer_id,
        },
    )


async def _decide(
    transfer_id: int, decision: str, session: AsyncSession, user: Principal
) -> TransferOut:
    existing = await redistribution.list_transfers(session, ids=[transfer_id])
    if not existing:
        # A donor can be looking at a recommendation a re-plan has just
        # replaced (fix list #37). Say so, with the time, rather than "not found".
        replaced_at = await _replaced_at(session, transfer_id)
        if replaced_at is not None:
            raise HTTPException(status_code=409, detail=redistribution.replaced_message(replaced_at))
        raise HTTPException(status_code=404, detail="Transfer not found")
    before = existing[0]
    ends = [before["from"]["id"], before["to"]["id"]]

    facilities = {
        f.id: f
        for f in (
            await session.execute(select(Facility).where(Facility.id.in_(ends)))
        ).scalars()
    }
    src, dst = facilities[ends[0]], facilities[ends[1]]
    allowed = can_decide_transfer(
        user,
        from_state=src.state_silo,
        from_district=src.district,
        to_state=dst.state_silo,
        to_district=dst.district,
        from_facility=src.id,
    )
    # The emergency drill runs in the Nashik sandbox in demo mode; an officer
    # running it acts for the donor there, and the drill says so on screen.
    in_sandbox = all(in_demo_sandbox(f.state_silo, f.district) for f in (src, dst))
    # A public demo account outside the sandbox is told that, not a vaguer
    # "outside your area".
    if not in_sandbox and is_public_demo(user):
        raise HTTPException(status_code=403, detail=demo_sandbox_refusal())
    if not allowed and settings.demo_mode and in_sandbox and user.role in (
        "admin", "state_officer", "block_mo"
    ):
        allowed = can_submit_reading(
            user, facility_id=src.id, facility_state=src.state_silo, facility_district=src.district
        )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="This transfer is outside the area you are responsible for",
        )
    # A decision moves stock at both ends, so a public demo account needs both
    # inside the sandbox.
    if not in_sandbox and is_public_demo(user):
        raise HTTPException(status_code=403, detail=demo_sandbox_refusal())

    status_before = {s.id: s.status for s in await services.get_snapshots(session, ends)}

    try:
        await redistribution.decide_transfer(
            session, transfer_id, decision,  # type: ignore[arg-type]
            actor_ref=f"user:{user.id}", actor_role=user.role,
        )
    except redistribution.TransferConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc))

    after_row = (await redistribution.list_transfers(session, ids=[transfer_id]))[0]

    if decision == "approved":
        for fid in ends:
            await services.refresh_facility_state(session, fid)
        snaps = {s.id: s for s in await services.get_snapshots(session, ends)}
        for fid, direction in ((ends[0], "out"), (ends[1], "in")):
            snap = snaps.get(fid)
            sku_row = next((x for x in (snap.skus if snap else []) if x.sku_code == before["sku_code"]), None)
            await events.record(
                session,
                events.READING_COMMITTED,
                {
                    "facility_id": fid,
                    "facility_name": snap.name if snap else fid,
                    "sku_code": before["sku_code"],
                    "qty_on_hand": sku_row.qty_on_hand if sku_row else None,
                    "source": "transfer",
                    "direction": direction,
                    "days_of_stock": sku_row.days_of_stock if sku_row else None,
                    "reporter_ref": f"transfer-{transfer_id}",
                },
                state_silo=dst.state_silo,
            )
            if snap and snap.status != status_before.get(fid):
                await events.record(
                    session,
                    events.STATUS_CHANGED,
                    {
                        "facility_id": fid,
                        "facility_name": snap.name,
                        "from": status_before.get(fid),
                        "to": snap.status,
                    },
                    state_silo=dst.state_silo,
                )
        recipient = snaps.get(ends[1])
        to_sku = next(
            (x for x in (recipient.skus if recipient else []) if x.sku_code == before["sku_code"]),
            None,
        )
        to_days_after = to_sku.days_of_stock if to_sku else None
    else:
        to_days_after = None

    await events.record(
        session,
        events.TRANSFER_DECIDED,
        {
            "transfer_id": transfer_id,
            "decision": decision,
            "sku_code": before["sku_code"],
            "qty": before["qty"],
            "from_id": ends[0],
            "from_name": before["from"]["name"],
            "to_id": ends[1],
            "to_name": before["to"]["name"],
            "to_days_after": to_days_after,
            "facility_id": ends[1],
            "facility_name": before["to"]["name"],
        },
        state_silo=dst.state_silo,
    )
    return TransferOut.model_validate(after_row)


@router.post("/transfers/{transfer_id}/approve", response_model=TransferOut, tags=["transfers"])
async def approve_transfer(
    transfer_id: int,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> TransferOut:
    return await _decide(transfer_id, "approved", session, user)


@router.post("/transfers/{transfer_id}/reject", response_model=TransferOut, tags=["transfers"])
async def reject_transfer(
    transfer_id: int,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> TransferOut:
    return await _decide(transfer_id, "rejected", session, user)


# --------------------------------------------------------------------------
# Facilities
# --------------------------------------------------------------------------
# Bounded on purpose. This endpoint reads each facility's recent readings to
# score it, so the cost is set by how many facilities come back, not by how
# many the caller wanted.
FACILITY_PAGE_DEFAULT = 200
FACILITY_PAGE_MAX = 500


@router.get("/facilities", response_model=list[FacilityOut], tags=["facilities"])
async def list_facilities(
    session: AsyncSession = Depends(get_session),
    district: str | None = Query(default=None),
    state_silo: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=FACILITY_PAGE_DEFAULT, ge=1, le=FACILITY_PAGE_MAX),
    user: Principal = Depends(current_user),
) -> list[FacilityOut]:
    """Facilities matching the filter, worst first, inside the caller's scope
    (fix #77): outside it the list is empty, and the districts roll-up is what
    the caller reads instead.

    The filter is resolved to a bounded set of facility ids in SQL *before*
    anything reads a stock reading. It used to be the other way round: every
    facility was scored and the district, state and status filters were applied
    to the result in Python. Scoring every facility means reading every
    reading in the burn-rate window — about 1.18M rows on the deployed database
    — so asking for one district cost the same as asking for the country, and
    on a 1 GB volume the sort behind it spilled roughly 70 MB of temporary
    files and never finished.

    `aggregates.find_facilities` already does this selection in one query,
    against the materialised snapshot the map reads, and it is the same
    ordering and the same status rule the map uses. Reusing it keeps the two
    from drifting apart.
    """
    narrowed = _narrow_to_rows_scope(user, state_silo, district)
    if narrowed is None:
        return []
    state_silo, district = narrowed
    pins = await aggregates.find_facilities(
        session, state=state_silo, district=district, status=status, limit=limit
    )
    if not pins:
        return []
    # Already worst-first from the query; keep that order rather than re-deriving
    # it, so the list and the map agree about which facility is most urgent.
    order = {p.id: i for i, p in enumerate(pins)}
    snaps = await services.get_snapshots(session, list(order))
    snaps.sort(key=lambda s: order.get(s.id, len(order)))
    return [_to_out(s) for s in snaps]


@router.get(
    "/facilities/{facility_id}",
    response_model=FacilityDetailOut,
    tags=["facilities"],
)
async def get_facility(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> FacilityDetailOut:
    await _readable_facility(session, facility_id, user)
    snaps = await services.get_snapshots(session, facility_ids=[facility_id], live_trust=True)
    if not snaps:
        raise HTTPException(status_code=404, detail="Facility not found")
    snap = snaps[0]
    return FacilityDetailOut(
        **_to_out(snap).model_dump(),
        skus=[SkuStockOut(**vars(s)) for s in snap.skus],
    )


# ==========================================================================
# The pharmacist's workspace — one facility, from its own rows.
#
# Three routes, all thin. Every decision they report is made by a pure
# function in `workspace.py` and tested in tests/test_workspace.py; what is
# left here is resolving the facility, checking who is asking, and shaping the
# reply. Each query underneath is bounded by one facility or one (state, sku)
# pair — none of them may grow into the national read that never returns.
# ==========================================================================


class LastReceiptOut(BaseModel):
    batch_id: str
    qty_received: float
    received_at: datetime
    received_via: str


class ProvenanceOut(BaseModel):
    """How the figure above it was last checked. `kind` is the word the screen
    shows; `detail` is the sentence under it."""

    kind: str
    at: datetime | None
    days_ago: int | None
    detail: str


class WorkspaceSkuOut(SkuStockOut):
    # The unit a pharmacist counts in — sachets, ampoules, blisters. Carried
    # here because a bare quantity on a phone screen has nothing beside it to
    # give it meaning, unlike the officer's table which has a column header.
    unit: str
    last_receipt: LastReceiptOut | None
    provenance: ProvenanceOut
    stockout_on: date | None
    # days_of_stock and status above are as of now, not as of the count
    # (fix #26); past the run-out date the shelf is overdue for a count.
    count_overdue: bool = False


class OwnRequestOut(BaseModel):
    """A request this centre raised in the last day, for its medicine card."""

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


class TodoOut(BaseModel):
    """One line of today's list (fix #85), computed, in both languages."""

    kind: str
    en: str
    hi: str
    tab: str


class LanguageOut(BaseModel):
    code: str
    name: str
    native: str


class WorkspaceOut(BaseModel):
    facility: FacilityOut
    skus: list[WorkspaceSkuOut]
    # Requests still waiting for a reply; one that lapsed does not count.
    open_requests: int
    max_open_requests: int
    # What this centre asked for in the last day, so each medicine card can
    # show its own request instead of offering to ask again (fix list #31).
    requests: list[OwnRequestOut]
    # Both languages of the computed line, always present and needing no key.
    # The screen renders this on load; the model is an overlay on top of it.
    briefing: dict[str, str]
    # Today's prioritised list, computed from this centre's rows (fix #85).
    todo: list[TodoOut] = []
    # The state's own language, where Gemini can write the list in it.
    local_language: LanguageOut | None = None
    # Active outbreaks in this centre's district, with its own cover at the
    # outbreak rate (fix #58). Empty when there is none: nothing is shown.
    outbreaks: list[dict] = []


def _require_demo_write(user: Principal, facility: Facility) -> None:
    """A public demo account changes nothing outside the sandbox district
    (auth.demo_may_write). Checked after the ordinary permission, so this
    refusal never tells a stranger more than that one would."""
    if not demo_may_write(user, state=facility.state_silo, district=facility.district):
        raise HTTPException(status_code=403, detail=demo_sandbox_refusal())


def _require_facts(user: Principal, facility: Facility) -> None:
    """Facts about a centre come from the centre itself (auth.can_report_facts).
    The sandbox rule is checked first, so a demo account outside the sandbox
    is told the rule that actually stopped it."""
    _require_demo_write(user, facility)
    if not can_report_facts(
        user,
        facility_id=facility.id,
        facility_state=facility.state_silo,
        facility_district=facility.district,
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Stock, deliveries, staff check-ins and beds are reported by the centre "
                "itself — only the centre can state them. Officers can view them and "
                "chase the centre for a report."
            ),
        )


def _require_own_handset(user: Principal, owner: Facility) -> None:
    """Sending as a registered handset is stating its centre's facts, so the
    same rule decides it (auth.can_report_facts; fix list #24). The refusal
    never names the centre that owns the number: a 403 must not become a way
    to look up whose number something is."""
    _require_demo_write(user, owner)
    if not can_report_facts(
        user,
        facility_id=owner.id,
        facility_state=owner.state_silo,
        facility_district=owner.district,
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Messages can be sent only as a handset registered to your own centre: "
                "a handset reports its centre's stock, deliveries, check-ins and beds, "
                "and only the centre can state them."
            ),
        )


async def _facility_in_scope(
    session: AsyncSession, facility_id: str, user: Principal
) -> Facility:
    """Resolve a facility the caller is entitled to work on.

    A refusal must not describe what was refused: naming the facility here
    would turn a 403 into a way of reading any centre's name out of the system
    one id at a time.
    """
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    if not can_view_facility(
        user,
        facility_id=facility.id,
        facility_state=facility.state_silo,
        facility_district=facility.district,
    ):
        if is_public_demo(user) and not in_demo_sandbox(facility.state_silo, facility.district):
            detail = demo_sandbox_refusal()
        elif user.role == "facility_user":
            detail = "You can only open the workspace for your own facility"
        else:
            detail = held_message(user, facility.state_silo)
        raise HTTPException(status_code=403, detail=detail)
    return facility


@router.get(
    "/facilities/{facility_id}/workspace",
    response_model=WorkspaceOut,
    tags=["workspace"],
)
async def facility_workspace(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> WorkspaceOut:
    """Everything one centre's staff need on one screen: what they hold, when
    it runs out, and how each figure was last checked."""
    facility = await _facility_in_scope(session, facility_id, user)
    snaps = await services.get_snapshots(session, facility_ids=[facility.id], live_trust=True)
    if not snaps:
        raise HTTPException(status_code=404, detail="Facility not found")
    snap = snaps[0]

    receipts = await workspace.last_receipts(session, facility.id)
    documents = await workspace.photo_documents(
        session,
        facility.id,
        {
            s.sku_code: s.last_reported_at
            for s in snap.skus
            if s.last_source == "photo" and s.last_reported_at is not None
        },
    )
    open_here, _ = await workspace.open_request_counts(session, facility.id)
    units = {
        row.code: row.unit
        for row in (await session.execute(select(Sku))).scalars().all()
    }
    now = datetime.now(timezone.utc)

    rows: list[WorkspaceSkuOut] = []
    for s in snap.skus:
        receipt = receipts.get(s.sku_code)
        prov = workspace.provenance(
            s.last_source, s.last_reported_at, receipt, now, document=documents.get(s.sku_code)
        )
        cover = workspace.cover_now(
            s.days_of_stock, s.last_reported_at, s.status, now, snap.warning_multiplier
        )
        rows.append(
            WorkspaceSkuOut(
                **{**vars(s), "days_of_stock": cover.days_of_stock, "status": cover.status},
                unit=units.get(s.sku_code, "unit"),
                last_receipt=LastReceiptOut(**vars(receipt)) if receipt else None,
                provenance=ProvenanceOut(**vars(prov)),
                # The run-out day is fixed by the count, so it comes from the
                # stored cover, not the countdown.
                stockout_on=workspace.stockout_date(s.days_of_stock, s.last_reported_at),
                count_overdue=cover.count_overdue,
            )
        )

    alerts = await _centre_alerts(
        session, facility, {r.sku_code: r.days_of_stock for r in rows}, now
    )
    brief_rows = _briefing_rows(snap.skus, units, now, snap.warning_multiplier)
    todo = await _todo_for(session, facility, snap.skus, brief_rows, alerts, units, now)
    local = workspace.state_language(facility.state_silo)

    return WorkspaceOut(
        facility=_to_out(snap),
        outbreaks=alerts,
        todo=[TodoOut(**vars(i)) for i in todo],
        local_language=LanguageOut(**vars(local)) if local else None,
        skus=rows,
        open_requests=open_here,
        max_open_requests=settings.max_open_requests_per_facility,
        requests=[
            OwnRequestOut(**vars(r)) for r in await workspace.own_requests(session, facility.id)
        ],
        briefing=workspace.rules_briefing(brief_rows),
    )


class DayUseOut(BaseModel):
    day: date
    used: float | None
    spread: bool = False


class UsageOut(BaseModel):
    """Fix #84: one medicine's use, burn rate and forecast, for its chart."""

    facility_id: str
    sku_code: str
    sku_name: str
    unit: str
    days: list[DayUseOut]
    burn_rate: float | None
    forecast_daily: float | None
    forecast_version: str | None
    forecast_published_at: datetime | None
    forecast_fresh: bool
    in_model: bool
    note: str


@router.get("/facilities/{facility_id}/usage", response_model=UsageOut, tags=["workspace"])
async def facility_usage(
    facility_id: str,
    sku: str = Query(..., min_length=1, max_length=16),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> UsageOut:
    """Bounded by one facility, one medicine and the burn window."""
    facility = await _readable_facility(session, facility_id, user)
    meta = await session.get(Sku, sku)
    if meta is None:
        raise HTTPException(status_code=404, detail="Unknown SKU")
    now = datetime.now(timezone.utc)
    window = settings.burn_rate_window_days
    rows = await session.execute(
        select(StockReading.reported_at, StockReading.qty_on_hand, StockReading.source)
        .where(
            StockReading.facility_id == facility.id,
            StockReading.sku_code == sku,
            StockReading.reported_at >= now - timedelta(days=window + 1),
            StockReading.superseded_by.is_(None),
        )
        .order_by(StockReading.reported_at)
    )
    series = [(at, float(qty), source) for at, qty, source in rows.all()]
    forecast = await session.get(Forecast, (facility.id, sku))
    fresh = bool(
        forecast is not None
        and settings.forecast_mode == "federated"
        and forecast.computed_at >= now - timedelta(days=settings.forecast_max_age_days)
    )
    in_model = facility.state_silo in workspace.MODEL_STATES and sku in workspace.MODEL_SKUS
    return UsageOut(
        facility_id=facility.id,
        sku_code=sku,
        sku_name=meta.name,
        unit=meta.unit,
        days=[
            DayUseOut(day=d.day, used=d.used, spread=d.spread)
            for d in workspace.daily_use(series, now, window)
        ],
        burn_rate=services._burn_rate(series),
        forecast_daily=forecast.predicted_daily_use if forecast is not None else None,
        forecast_version=forecast.model_version if forecast is not None else None,
        forecast_published_at=forecast.computed_at if forecast is not None else None,
        forecast_fresh=fresh,
        in_model=in_model,
        note=workspace.forecast_note(
            in_model=in_model,
            fresh=fresh,
            published=forecast.computed_at if forecast is not None else None,
            version=forecast.model_version if forecast is not None else None,
        ),
    )


class BriefingOut(BaseModel):
    """Today's list, in one language.

    `ai` is the only thing the screen may use to decide whether to put a model's
    name on it. When the model is unavailable, out of quota or switched off,
    this comes back with the computed list, `source="rules"` and
    `ai=False` — the app never presents computed text as a model's work.
    """

    # The list, most urgent first. `body` is the same lines joined.
    lines: list[str] = []
    body: str
    lang: str
    source: str            # "rules" | "gemini"
    ai: bool
    model: str | None
    generated_at: datetime | None
    cached: bool
    # Why the live path did not run, when it did not. Shown quietly, because a
    # pharmacist is entitled to know the difference between "nothing to add"
    # and "the model is out of quota".
    note: str | None = None


def _briefing_rows(
    skus: list[services.SkuStock],
    units: dict[str, str],
    now: datetime,
    warning_multiplier: float,
) -> list[workspace.BriefingRow]:
    """The Today card's rows, on the same as-of-now cover as the medicine
    cards beside it (fix #26)."""
    rows = []
    for s in skus:
        cover = workspace.cover_now(
            s.days_of_stock, s.last_reported_at, s.status, now, warning_multiplier
        )
        rows.append(
            workspace.BriefingRow(
                sku_name=s.sku_name,
                unit=units.get(s.sku_code, "unit"),
                qty=s.qty_on_hand,
                days_of_stock=cover.days_of_stock,
                status=cover.status,
                last_counted_on=(
                    s.last_reported_at.date()
                    if cover.count_overdue and s.last_reported_at
                    else None
                ),
                ran_out_on=(
                    workspace.stockout_date(s.days_of_stock, s.last_reported_at)
                    if cover.count_overdue
                    else None
                ),
            )
        )
    return rows


async def _centre_alerts(
    session: AsyncSession, facility: Facility, cover: dict[str, float | None], now: datetime
) -> list[dict]:
    """Active outbreaks in this centre's district, with its own cover at the
    outbreak rate (fix #58). `cover` is its as-of-now cover per medicine."""
    return [
        outbreak.centre_alert(await outbreak.evaluate(session, row, now), cover)
        for row in await outbreak.active(session, now, facility.state_silo)
        if row.district == facility.district
    ]


async def _waiting_on(session: AsyncSession, facility_id: str, now: datetime) -> list[dict]:
    """Requests waiting for this centre, as the donor, to accept or decline.
    A request that lapsed without a reply is no longer waiting, and could not
    be accepted anyway (fix list #31), so it is not listed."""
    rows = await redistribution.list_transfers(
        session, from_facility=facility_id, statuses=["proposed"], limit=50
    )
    window = redistribution.request_window()
    return [
        r for r in rows
        if not (
            r["triggered_by"] == workspace.FACILITY_REQUEST
            and redistribution.request_lapsed(r["created_at"], now, window)
        )
    ]


async def _todo_for(
    session: AsyncSession,
    facility: Facility,
    skus: list[services.SkuStock],
    rows: list[workspace.BriefingRow],
    alerts: list[dict],
    units: dict[str, str],
    now: datetime,
) -> list[workspace.TodoItem]:
    """Today's list for one centre (fix #85). Every read is bounded by the
    centre: its open consignments, its requests, its last ward report and its
    check-ins."""
    open_rows = [
        *await movements.list_movements(
            session, facility_id=facility.id, view=movements.OVERDUE, limit=10
        ),
        *await movements.list_movements(
            session, facility_id=facility.id, view=movements.OPEN, limit=10
        ),
    ]
    senders: dict[str, str] = {}
    for m in open_rows:
        if m.from_ref in senders:
            continue
        donor = await session.get(Facility, m.from_ref) if m.dispatch_source == "transfer" else None
        senders[m.from_ref] = donor.name if donor else "warehouse {0}".format(m.from_ref)
    deliveries = [
        workspace.TodoDelivery(
            sku_name=m.sku_name, unit=m.unit, qty=m.qty_dispatched,
            from_name=senders[m.from_ref],
            expected_on=workspace.in_india(m.expected_by).date(),
            overdue=m.status == movements.OVERDUE,
        )
        for m in open_rows
    ]

    names = {s.sku_code: s.sku_name for s in skus}
    own_waiting = [
        workspace.TodoRequest(
            sku_name=names.get(r.sku_code, r.sku_code), unit=units.get(r.sku_code, "unit"),
            qty=r.qty, donor=r.from_name,
        )
        for r in await workspace.own_requests(session, facility.id, now=now)
        if r.status == "proposed" and not r.lapsed
    ]

    bed_today: bool | None = None
    if facility.beds_total:
        latest = await beds.recent_reports(session, facility.id, limit=1)
        bed_today = bool(latest) and (
            workspace.in_india(latest[0].reported_at).date() == workspace.in_india(now).date()
        )

    return workspace.todo_items(
        rows=rows,
        deliveries=deliveries,
        awaiting_your_reply=len(await _waiting_on(session, facility.id, now)),
        own_waiting=own_waiting,
        outbreaks=[a["headline"] for a in alerts],
        bed_report_today=bed_today,
        checked_in_today=(await attendance.summarise(session, facility.id)).present,
    )


def _rules_reply(
    items: list[workspace.TodoItem],
    lang: str,
    local: workspace.StateLanguage | None,
    note: str | None,
) -> BriefingOut:
    """The computed list as the answer. The state's language has no computed
    wording — only the model writes it — so it falls back to English and says
    so rather than passing a translation off as checked."""
    lines = [i.hi if lang == "hi" else i.en for i in items]
    if local and lang == local.code:
        note = "The {0} list is written by Gemini; showing the computed list in English.{1}".format(
            local.name, " " + note if note else ""
        )
    return BriefingOut(
        lines=lines, body="\n".join(lines), lang=lang, source="rules", ai=False,
        model=None, generated_at=None, cached=False, note=note,
    )


@router.post(
    "/facilities/{facility_id}/briefing",
    response_model=BriefingOut,
    tags=["workspace"],
)
async def facility_briefing(
    facility_id: str,
    lang: str = Query(default="en", pattern="^[a-z]{2}$"),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> BriefingOut:
    """Ask the model to write today's list. POST, and only ever from a click.

    Never called on page load: the free tier allows 20 generate requests a day
    per model, and a screen that spent one on every render would be out of
    quota before the first demo finished. The computed list already travels
    with the workspace payload, so this endpoint is an overlay on something
    that is already on screen, not the thing that fills it. One call writes
    every language, and all of them are cached together.
    """
    facility = await _facility_in_scope(session, facility_id, user)
    snaps = await services.get_snapshots(session, facility_ids=[facility.id], live_trust=True)
    if not snaps:
        raise HTTPException(status_code=404, detail="Facility not found")
    snap = snaps[0]

    local = workspace.state_language(facility.state_silo)
    if lang not in {"en", "hi"} | ({local.code} if local else set()):
        raise HTTPException(
            status_code=422, detail="This centre's list is not written in that language"
        )

    units = {
        row.code: row.unit for row in (await session.execute(select(Sku))).scalars().all()
    }
    now = datetime.now(timezone.utc)
    rows = _briefing_rows(snap.skus, units, now, snap.warning_multiplier)
    alerts = await _centre_alerts(
        session, facility,
        {
            s.sku_code: workspace.cover_now(
                s.days_of_stock, s.last_reported_at, s.status, now, snap.warning_multiplier
            ).days_of_stock
            for s in snap.skus
        },
        now,
    )
    items = await _todo_for(session, facility, snap.skus, rows, alerts, units, now)
    inputs_hash = workspace.todo_hash(items)

    if not vision.briefing_available():
        return _rules_reply(items, lang, local, None)

    cached = await workspace.cached_briefing(session, facility.id, lang, inputs_hash)
    if cached is not None:
        return BriefingOut(
            lines=cached.body.split("\n"), body=cached.body, lang=lang, source="gemini",
            ai=True, model=cached.model, generated_at=cached.generated_at, cached=True,
        )

    try:
        written = await vision.write_briefing(
            facility_name=facility.name,
            rows=[i.en for i in items],
            local=(local.code, local.name) if local else None,
        )
    except vision.VisionError as exc:
        # One attempt, then the computed list. No retry loop: the caller is a
        # person clicking a button, and a spent quota does not recover in the
        # time it takes to try again.
        return _rules_reply(items, lang, local, "Showing the computed list — {0}.".format(exc))

    await workspace.store_briefing(
        session, facility.id,
        {code: "\n".join(lines) for code, lines in written.lines.items()},
        inputs_hash=inputs_hash, model=written.model,
    )
    await session.commit()

    return BriefingOut(
        lines=written.lines[lang], body="\n".join(written.lines[lang]),
        lang=lang, source="gemini", ai=True, model=written.model,
        generated_at=datetime.now(timezone.utc), cached=False,
    )


class StockPhotoLineOut(BaseModel):
    """One line the model read, and what became of it."""

    medicine: str
    quantity: float
    # As printed on the document, or None when it does not say.
    unit: str | None = None
    batch: str | None = None
    sku_code: str | None
    sku_name: str | None
    match_score: int
    committed: bool
    # "added" (a delivery, through the ledger), "subtracted" (an issue), "set"
    # (a count) or "not_applied" — never a bare "recorded".
    action: str
    # The shelf figure before and after this line, so the screen shows the
    # ledger change rather than only that something happened.
    qty_before: float | None = None
    qty_after: float | None = None
    # The dispatch a delivery slip settled, when it settled one.
    movement_id: int | None = None
    # Why a line was not applied, when it was not. Shown to the pharmacist,
    # because "three of four lines went in" without saying which is worse than
    # refusing the lot.
    reason: str | None = None


class StockPhotoOut(BaseModel):
    facility_id: str
    model: str
    # False whenever the deterministic mock produced this. The screen labels a
    # real extraction with the model's name and this one as a mock; computed
    # output is never presented as the model's work.
    ai: bool
    # The model's own estimate of how well it read the page. Not a check:
    # nothing here verifies it, and the screen says so.
    confidence: float
    # delivery_slip | issue_record | stock_count | unknown — what decided
    # whether each number was added, subtracted or set.
    document_type: str
    document_date: date | None
    document_age_days: int | None
    notes: str | None
    lines: list[StockPhotoLineOut]
    committed: int


class StockPhotoIn(BaseModel):
    image_base64: str
    image_mime: str = "image/jpeg"


@router.post(
    "/facilities/{facility_id}/stock-photo",
    response_model=StockPhotoOut,
    tags=["workspace"],
)
async def submit_stock_photo(
    facility_id: str,
    payload: StockPhotoIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> StockPhotoOut:
    """Read a photographed stock document and change the shelf the way that
    document means it (spec 26.3–26.4, fix list #11).

    The model says which document it is looking at, and that decides what each
    number does (app/stockphoto.py): a delivery slip is added through the
    delivery ledger, settling its own dispatch, so it cannot be counted twice;
    an issue record is subtracted; a stock count is set. Reading every line as
    the new shelf level is how a slip for 10 tablets once emptied a shelf of
    500 on the map.

    It refuses more than it accepts, because the model turns a photograph into
    rows the reorder threshold, the forecast and the solver all read next. A
    line is applied only when its medicine resolves to this centre's list and
    nothing about it is doubtful; otherwise it comes back "not applied" with
    the reason, and nothing about it is stored.

    The photograph itself is never stored. Only what was read from it is.
    """
    facility = await _facility_in_scope(session, facility_id, user)
    _require_facts(user, facility)

    try:
        image = base64.b64decode(payload.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Unreadable image data") from exc

    try:
        extraction = await vision.read_stock_photo(image, payload.image_mime)
    except vision.VisionError as exc:
        # Nothing is written and nothing is guessed. The pharmacist is told
        # what happened and can re-take the photo or type the figures instead.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    lookup = await ingest.sku_lookup(session)
    skus = {s.code: s for s in (await session.execute(select(Sku))).scalars().all()}
    now = datetime.now(timezone.utc)
    today = now.date()
    rows: list[StockPhotoLineOut] = []
    committed = 0
    readings_written = 0
    snaps = await services.get_snapshots(session, facility_ids=[facility.id])
    on_hand = {s.sku_code: float(s.qty_on_hand) for s in (snaps[0].skus if snaps else [])}
    last_count = await workspace.last_count_times(
        session, facility.id, since=now - timedelta(days=settings.stock_photo_max_age_days + 30)
    )
    open_by_sku, settled_by_sku = await movements.deliveries_to(
        session, facility.id, since=now - timedelta(days=30)
    )
    settled_ids: set[int] = set()

    for i, line in enumerate(extraction.lines):
        code, score = ingest.resolve_sku(line.medicine, lookup)
        common = dict(
            medicine=line.medicine, quantity=line.quantity, unit=line.unit,
            batch=line.batch, match_score=score,
        )
        if code is None:
            rows.append(
                StockPhotoLineOut(
                    **common, sku_code=None, sku_name=None, committed=False,
                    action="not_applied",
                    reason="No medicine in this centre's list matches that name.",
                )
            )
            continue

        sku = skus[code]
        before = on_hand.get(code)
        decision = stockphoto.decide_line(
            document_type=extraction.document_type,
            qty=line.quantity,
            printed_unit=line.unit,
            printed_batch=line.batch,
            sku_name=sku.name,
            sku_unit=sku.unit,
            on_hand=before or 0.0,
            last_count_at=last_count.get(code),
            document_date=extraction.document_date,
            today=today,
            confidence=extraction.confidence,
            open_deliveries=[
                d for d in open_by_sku.get(code, []) if d.movement_id not in settled_ids
            ],
            settled_deliveries=settled_by_sku.get(code, []),
            confidence_floor=settings.channel_confidence_floor,
            max_age_days=settings.stock_photo_max_age_days,
        )
        line_out = dict(common, sku_code=code, sku_name=sku.name, qty_before=before)

        if decision.action == "hold":
            rows.append(
                StockPhotoLineOut(
                    **line_out, committed=False, action="not_applied", reason=decision.reason
                )
            )
            continue

        if decision.action == "receive":
            # A delivery goes through the ledger, never around it: the receipt
            # settles the dispatch and writes the stock itself, so the same
            # slip photographed twice finds its batch already settled.
            try:
                _, effect = await movements.confirm_receipt(
                    session, decision.movement_id,
                    qty_received=line.quantity, via="photo", by_ref=f"user:{user.id}",
                    note=f"Read from a photographed delivery slip by {extraction.model}",
                )
            except movements.ReceiptError as exc:
                rows.append(
                    StockPhotoLineOut(
                        **line_out, committed=False, action="not_applied", reason=str(exc)
                    )
                )
                continue
            settled_ids.add(decision.movement_id)
            on_hand[code] = effect["qty_on_hand"]
            committed += 1
            rows.append(
                StockPhotoLineOut(
                    **line_out, committed=True, action="added",
                    qty_after=effect["qty_on_hand"], movement_id=decision.movement_id,
                )
            )
            continue

        session.add(
            StockReading(
                facility_id=facility.id,
                sku_code=code,
                qty_on_hand=Decimal(str(decision.qty_after)),
                # Two lines of one medicine on one page keep their order.
                reported_at=now + timedelta(microseconds=i),
                source="photo",
                reporter_ref=f"user:{user.id}",
                # The model's own confidence, carried through rather than
                # replaced: a blurred page must read as a doubtful row, and the
                # trust layer widens this facility's warning thresholds for it.
                confidence=Decimal(str(round(extraction.confidence, 2))),
                raw_payload={
                    "read_by": extraction.model,
                    "document_type": extraction.document_type,
                    "as_printed": line.medicine,
                    "qty_as_printed": line.quantity,
                    "unit_as_printed": line.unit,
                    "match_score": score,
                    "document_date": (
                        extraction.document_date.isoformat()
                        if extraction.document_date
                        else None
                    ),
                },
            )
        )
        on_hand[code] = decision.qty_after
        committed += 1
        readings_written += 1
        rows.append(
            StockPhotoLineOut(
                **line_out, committed=True,
                action="subtracted" if decision.action == "subtract" else "set",
                qty_after=decision.qty_after,
            )
        )

    if committed:
        await session.commit()
        if readings_written:
            await services.refresh_facility_state(session, facility.id)
            await session.commit()
        await events.record(
            session,
            events.READING_COMMITTED,
            {
                "facility_id": facility.id,
                "facility_name": facility.name,
                "source": "photo",
                "document_type": extraction.document_type,
                "lines": committed,
                "read_by": extraction.model,
            },
            state_silo=facility.state_silo,
        )

    return StockPhotoOut(
        facility_id=facility.id,
        model=extraction.model,
        ai=extraction.model != "mock",
        confidence=extraction.confidence,
        document_type=extraction.document_type,
        document_date=extraction.document_date,
        document_age_days=(
            (today - extraction.document_date).days if extraction.document_date else None
        ),
        notes=extraction.notes,
        lines=rows,
        committed=committed,
    )


class DonorOut(BaseModel):
    facility_id: str
    name: str
    district: str
    lat: float
    lng: float
    km: float
    # 'straight_line_x1.3'. Never 'google_routes' while MAPS_MODE is osm: the
    # UI must not imply a road route when it is showing a straight line.
    distance_basis: str
    spare_units: int
    days_kept: float


class SupplyOut(BaseModel):
    sku_code: str
    sku_name: str
    unit: str
    units_needed: int
    donors: list[DonorOut]
    manual_only: bool
    reason: str | None


@router.get(
    "/facilities/{facility_id}/supply", response_model=SupplyOut, tags=["workspace"]
)
async def facility_supply(
    facility_id: str,
    sku: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> SupplyOut:
    """The nearest centres that can spare this medicine and still hold their
    own safety stock — the solver's own split, read from the short centre's
    side, so nothing is offered that an officer would have to refuse."""
    facility = await _facility_in_scope(session, facility_id, user)
    sku_row = await session.get(Sku, sku)
    if sku_row is None:
        raise HTTPException(status_code=404, detail="Unknown medicine")

    rule = workspace.SkuRule(
        code=sku_row.code, name=sku_row.name, unit=sku_row.unit,
        is_controlled=bool(sku_row.is_controlled), cold_chain=bool(sku_row.cold_chain),
    )
    nodes_by_sku = await redistribution.load_state_nodes(
        session, facility.state_silo, sku
    )
    result = workspace.rank_donors(
        nodes_by_sku.get(sku, []),
        redistribution.PlanRules.from_settings(),
        recipient_id=facility.id,
        sku=rule,
    )
    return SupplyOut(
        sku_code=rule.code, sku_name=rule.name, unit=rule.unit,
        units_needed=result.units_needed,
        donors=[DonorOut(**vars(d)) for d in result.donors],
        manual_only=result.manual_only, reason=result.reason,
    )


class RequestIn(BaseModel):
    sku_code: str = Field(min_length=1)
    from_facility: str = Field(min_length=1)
    qty: float = Field(gt=0)


class RequestOut(BaseModel):
    transfer_id: int
    reference: str
    status: str
    sku_code: str
    # The name and the unit travel with the receipt because it is printed and
    # filed: "461 capsules of Amoxicillin 250mg" is a document a district
    # office can act on, "461 AMOX" is a line only this system understands.
    sku_name: str
    unit: str
    qty: float
    from_facility: str
    from_name: str
    to_facility: str
    # Which role may decide this one. block_mo can only decide inside its own
    # district (auth.can_decide_transfer), so a cross-district donor needs the
    # state officer. Naming it beats leaving the pharmacist to guess.
    approver_role: str
    km: float
    distance_basis: str
    eta_hours: float
    estimated_delivery: date
    estimate_label: str
    assumptions: dict[str, float]
    # The request's state in words, e.g. "Awaiting reply from Nashik PHC 13
    # (pharmacist)" — the screen never shows `status` or `approver_role` raw.
    status_words: str
    # The approval the estimate depends on (the dispatch cutoff, India time):
    # "If approved by 14:00 today, about 1 Oct".
    approve_by: datetime
    # When the request lapses if nobody replies (fix list #31).
    lapses_at: datetime


@router.get(
    "/facilities/{facility_id}/incoming", response_model=list[TransferOut], tags=["workspace"]
)
async def incoming_requests(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[TransferOut]:
    """Requests waiting for this centre, as the donor, to accept or decline.
    A request that lapsed without a reply is no longer waiting, and could not
    be accepted anyway (fix list #31), so it is not listed."""
    facility = await _facility_in_scope(session, facility_id, user)
    return [
        TransferOut.model_validate(r)
        for r in await _waiting_on(session, facility.id, datetime.now(timezone.utc))
    ]


@router.post("/transfers/{transfer_id}/cancel", response_model=TransferOut, tags=["workspace"])
async def cancel_request(
    transfer_id: int,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> TransferOut:
    """Withdraw a request this centre raised, before the donor replies.

    Only the centre that raised it, and only while it is still a request: the
    plan's own recommendations are changed by re-running the plan, and a
    decided transfer is history. Taken under the same row lock the donor's
    decision takes, so a cancel and an accept cannot both win.
    """
    transfer = await session.get(Transfer, transfer_id)
    if transfer is None:
        raise HTTPException(status_code=404, detail="Request not found")
    facility = await session.get(Facility, transfer.to_facility)
    if user.role != "facility_user" or user.facility_id != transfer.to_facility:
        raise HTTPException(
            status_code=403, detail="Only the centre that raised a request can cancel it"
        )
    _require_demo_write(user, facility)
    if transfer.triggered_by != workspace.FACILITY_REQUEST:
        raise HTTPException(
            status_code=409,
            detail="This is the plan's recommendation, not a request; it changes when the plan is re-run.",
        )
    locked = await session.get(Transfer, transfer_id, with_for_update=True, populate_existing=True)
    if locked.status != "proposed":
        raise HTTPException(
            status_code=409,
            detail="This request is already closed: {0}.".format(
                workspace.request_words(locked.status, False, "the donor centre").lower()
            ),
        )
    locked.status = "cancelled"
    session.add(
        Approval(
            transfer_id=locked.id, actor_ref=f"user:{user.id}", actor_role=user.role,
            decision="cancelled", channel="web",
        )
    )
    await session.commit()
    await events.record(
        session,
        events.TRANSFER_CANCELLED,
        {"transfer_id": locked.id, "sku_code": locked.sku_code, "facility_id": facility.id,
         "facility_name": facility.name},
        state_silo=facility.state_silo,
    )
    return TransferOut.model_validate((await redistribution.list_transfers(session, ids=[locked.id]))[0])


@router.post(
    "/facilities/{facility_id}/demo-request",
    response_model=TransferOut,
    status_code=201,
    tags=["workspace"],
)
async def demo_neighbour_request(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> TransferOut:
    """Demo only: the neighbouring centre shortest on something this centre can
    spare raises a real request to it, so one login can show both sides.
    The request is an ordinary proposed transfer; nothing moves until this
    centre accepts it."""
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    facility = await _facility_in_scope(session, facility_id, user)
    _require_demo_write(user, facility)
    controlled = set(
        (await session.execute(select(Sku.code).where(Sku.is_controlled.is_(True)))).scalars()
    )
    rules = redistribution.PlanRules.from_settings()
    nodes_by_sku = await redistribution.load_state_nodes(session, facility.state_silo)
    best = None
    for sku, nodes in nodes_by_sku.items():
        if sku in controlled:
            continue
        me = next((n for n in nodes if n.facility_id == facility.id), None)
        if me is None or me.burn <= 0:
            continue
        spare = me.qty - rules.donor_floor_days * me.burn
        neighbours = [
            n for n in nodes
            if n.facility_id != facility.id and n.district == facility.district and n.burn > 0
        ]
        if spare < 1 or not neighbours:
            continue
        needy = min(neighbours, key=lambda n: n.qty / n.burn)
        qty = round(min(spare, max(rules.recipient_target_days * needy.burn - needy.qty, 3 * needy.burn)))
        if qty >= 1 and (best is None or needy.qty / needy.burn < best[0]):
            best = (needy.qty / needy.burn, sku, needy, qty, me)
    if best is None:
        raise HTTPException(
            status_code=409, detail="No medicine here has stock to spare for a neighbour right now"
        )
    _, sku, needy, qty, me = best
    km = workspace.road_km_between(me, needy, rules.road_factor)
    transfer = Transfer(
        from_facility=facility.id, to_facility=needy.facility_id, sku_code=sku, qty=qty,
        route_km=round(km, 1), eta_hours=round(km / rules.avg_speed_kmh + rules.handling_hours, 2),
        route_source="haversine", status="proposed", triggered_by=workspace.FACILITY_REQUEST,
        rationale={
            "origin": "facility_request", "approver_role": "donor_facility", "demo": True,
            "units_needed": qty, "recipient_days_before": round(needy.qty / needy.burn, 2),
            "recipient_days_after_this": round((needy.qty + qty) / needy.burn, 2),
            "donor_days_after_plan": round((me.qty - qty) / me.burn, 2),
            "recipient_status": "critical" if needy.qty / needy.burn < settings.critical_days else "at_risk",
        },
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    await events.record(
        session, events.TRANSFER_REQUESTED,
        {"transfer_id": transfer.id, "reference": workspace.reference(transfer.id),
         "facility_id": needy.facility_id, "facility_name": needy.name, "sku_code": sku,
         "qty": qty, "from_name": facility.name},
        state_silo=facility.state_silo,
    )
    return TransferOut.model_validate((await redistribution.list_transfers(session, ids=[transfer.id]))[0])


@router.post(
    "/facilities/{facility_id}/requests",
    response_model=RequestOut,
    status_code=201,
    tags=["workspace"],
)
async def create_request(
    facility_id: str,
    payload: RequestIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> RequestOut:
    """Raise a request for stock. This creates a *proposed* transfer and
    nothing else: no stock moves until the donor centre accepts it (spec 12.3 —
    nothing auto-executes)."""
    facility = await _facility_in_scope(session, facility_id, user)
    _require_demo_write(user, facility)
    sku_row = await session.get(Sku, payload.sku_code)
    if sku_row is None:
        raise HTTPException(status_code=404, detail="Unknown medicine")
    donor_facility = await session.get(Facility, payload.from_facility)
    if donor_facility is None:
        raise HTTPException(status_code=404, detail="Unknown donor facility")
    if donor_facility.id == facility.id:
        raise HTTPException(
            status_code=422, detail="A centre cannot request stock from itself"
        )

    open_here, open_overall = await workspace.open_request_counts(session, facility.id)
    verdict = workspace.check_caps(
        open_here, open_overall, workspace.CapLimits.from_settings()
    )
    if not verdict.allowed:
        raise HTTPException(status_code=409, detail=verdict.reason)
    # One live request per medicine: a second would ask two donors for the
    # same shortfall, and the medicine card shows the first instead of a
    # "Find supply" button while it waits (fix list #31).
    waiting = next(
        (
            r for r in await workspace.own_requests(session, facility.id)
            if r.sku_code == payload.sku_code and r.status == "proposed" and not r.lapsed
        ),
        None,
    )
    if waiting is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This centre already has a request for {0} waiting on {1}, sent at {2}. "
                "Cancel it first, or wait for the reply."
            ).format(
                sku_row.name, waiting.from_name,
                workspace.in_india(waiting.created_at).strftime("%H:%M"),
            ),
        )

    rule = workspace.SkuRule(
        code=sku_row.code, name=sku_row.name, unit=sku_row.unit,
        is_controlled=bool(sku_row.is_controlled), cold_chain=bool(sku_row.cold_chain),
    )
    nodes_by_sku = await redistribution.load_state_nodes(
        session, donor_facility.state_silo, payload.sku_code
    )
    nodes = nodes_by_sku.get(payload.sku_code, [])
    donor_node = next((n for n in nodes if n.facility_id == donor_facility.id), None)
    if donor_node is None:
        raise HTTPException(
            status_code=422, detail="That centre does not stock this medicine"
        )

    rules = redistribution.PlanRules.from_settings()
    try:
        workspace.validate_request(donor_node, payload.qty, sku=rule, rules=rules)
    except workspace.RequestRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    me_node = next((n for n in nodes if n.facility_id == facility.id), None)
    km = (
        workspace.road_km_between(donor_node, me_node, rules.road_factor)
        if me_node
        else 0.0
    )
    now = datetime.now(timezone.utc)
    # The dispatch cutoff is a local hour, so the estimate reads India time;
    # reading the UTC hour put every request raised 14:00–19:30 on the wrong day.
    estimate = workspace.delivery_estimate(km=km, raised_at=workspace.in_india(now))
    eta_hours = round(km / rules.avg_speed_kmh + rules.handling_hours, 2)
    # The donor centre's staff accept or decline (auth.can_decide_transfer).
    approver_role = "donor_facility"

    transfer = Transfer(
        from_facility=donor_facility.id,
        to_facility=facility.id,
        sku_code=payload.sku_code,
        qty=payload.qty,
        route_km=round(km, 1),
        eta_hours=eta_hours,
        route_source="haversine",
        status="proposed",
        triggered_by=workspace.FACILITY_REQUEST,
        rationale={
            "origin": "facility_request",
            "requested_by_role": user.role,
            "approver_role": approver_role,
            "units_needed": payload.qty,
            "donor_days_kept": round((donor_node.qty - payload.qty) / donor_node.burn, 2),
            "distance_basis": estimate.basis,
            "estimate_label": estimate.label,
            "assumptions": estimate.assumptions,
        },
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)

    await events.record(
        session,
        events.TRANSFER_REQUESTED,
        {
            "transfer_id": transfer.id,
            "reference": workspace.reference(transfer.id),
            "facility_id": facility.id,
            "facility_name": facility.name,
            "sku_code": payload.sku_code,
            "qty": payload.qty,
            "from_name": donor_facility.name,
        },
        state_silo=facility.state_silo,
    )

    return RequestOut(
        transfer_id=transfer.id,
        reference=workspace.reference(transfer.id),
        status=transfer.status,
        sku_code=payload.sku_code,
        sku_name=rule.name,
        unit=rule.unit,
        qty=payload.qty,
        from_facility=donor_facility.id,
        from_name=donor_facility.name,
        to_facility=facility.id,
        approver_role=approver_role,
        km=round(km, 1),
        distance_basis=estimate.basis,
        eta_hours=eta_hours,
        estimated_delivery=estimate.expected_on,
        estimate_label=estimate.label,
        assumptions={k: float(v) for k, v in estimate.assumptions.items()},
        status_words=workspace.request_words("proposed", False, donor_facility.name),
        approve_by=estimate.approve_by,
        lapses_at=transfer.created_at + redistribution.request_window(),
    )


# --------------------------------------------------------------------------
# Stock readings — the web-form path into the ingestion spine.
# Every other channel (SMS, IVR, WhatsApp, offline queue) funnels through this
# same commit-and-emit logic in Week 3 rather than duplicating it.
# --------------------------------------------------------------------------
@router.post("/stock/readings", response_model=StockReadingOut, tags=["stock"])
async def submit_reading(
    payload: StockReadingIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> StockReadingOut:
    facility = await session.get(Facility, payload.facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Unknown facility")
    _require_facts(user, facility)
    # "seed" and "transfer" are written only by the server itself. A browser
    # able to claim "transfer" could hide real consumption, because transfer
    # readings are excluded from usage rates. Phone-channel sources come from
    # their webhook adapters, and through here only in demo mode.
    if payload.source not in WEB_SOURCES and not (
        settings.demo_mode and payload.source in DEMO_CHANNEL_SOURCES
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Source '{payload.source}' cannot be submitted through this endpoint",
        )
    if payload.source in WEB_SOURCES or not payload.reporter_ref:
        payload.reporter_ref = f"user:{user.id}"
    if await session.get(Sku, payload.sku_code) is None:
        raise HTTPException(status_code=404, detail="Unknown SKU")

    # Idempotency: a retried Twilio webhook must not double-count (spec 13).
    if payload.channel_msg_id:
        existing = await session.scalar(
            select(StockReading).where(
                StockReading.channel_msg_id == payload.channel_msg_id
            )
        )
        if existing is not None:
            snaps = await services.get_snapshots(session, [payload.facility_id])
            status_now = snaps[0].status if snaps else services.STATUS_HEALTHY
            return StockReadingOut(
                id=existing.id,
                facility_id=existing.facility_id,
                sku_code=existing.sku_code,
                qty_on_hand=float(existing.qty_on_hand),
                days_of_stock=None,
                status_before=status_now,
                status_after=status_now,
                status_changed=False,
                duplicate=True,
            )

    before = await services.get_snapshots(session, [payload.facility_id])
    status_before = before[0].status if before else services.STATUS_HEALTHY

    reading = StockReading(
        facility_id=payload.facility_id,
        sku_code=payload.sku_code,
        qty_on_hand=payload.qty_on_hand,
        reported_at=payload.reported_at or datetime.now(timezone.utc),
        source=payload.source,
        footfall_same_day=payload.footfall_same_day,
        reporter_ref=payload.reporter_ref,
        channel_msg_id=payload.channel_msg_id,
        confidence=payload.confidence,
    )
    session.add(reading)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate reading")
    await session.refresh(reading)

    # Keep the map's snapshot table in step with the readings it summarises.
    await services.refresh_facility_state(session, payload.facility_id)

    after = await services.get_snapshots(session, [payload.facility_id])
    snap = after[0] if after else None
    status_after = snap.status if snap else services.STATUS_HEALTHY
    dos = next(
        (
            s.days_of_stock
            for s in (snap.skus if snap else [])
            if s.sku_code == payload.sku_code
        ),
        None,
    )

    await events.record(
        session,
        events.READING_COMMITTED,
        {
            "facility_id": payload.facility_id,
            "facility_name": facility.name,
            "sku_code": payload.sku_code,
            "qty_on_hand": payload.qty_on_hand,
            "source": payload.source,
            "confidence": payload.confidence,
            "days_of_stock": dos,
            "reporter_ref": payload.reporter_ref,
        },
        state_silo=facility.state_silo,
    )
    if status_after != status_before:
        await events.record(
            session,
            events.STATUS_CHANGED,
            {
                "facility_id": payload.facility_id,
                "facility_name": facility.name,
                "from": status_before,
                "to": status_after,
            },
            state_silo=facility.state_silo,
        )

    return StockReadingOut(
        id=reading.id,
        facility_id=reading.facility_id,
        sku_code=reading.sku_code,
        qty_on_hand=float(reading.qty_on_hand),
        days_of_stock=dos,
        status_before=status_before,
        status_after=status_after,
        status_changed=status_after != status_before,
    )


# --------------------------------------------------------------------------
# Live updates — polled (see events.py for why not a stream)
# --------------------------------------------------------------------------
class EventOut(BaseModel):
    id: int
    kind: str
    created_at: datetime
    data: dict


class EventsOut(BaseModel):
    cursor: int
    reset: bool
    server_time: datetime
    events: list[EventOut]


def scope_events(
    feed: list[dict], where: dict[str, tuple[str, str]], user: Principal
) -> list[dict]:
    """The event feed as this reader may see it (fix #77). Every event still
    arrives — the screen needs to know something changed — but a payload about
    a centre whose rows the reader may not read is withheld. `where` maps each
    facility id in the batch to its (state, district)."""
    out = []
    for e in feed:
        fid = (e.get("data") or {}).get("facility_id")
        place = where.get(fid) if fid else None
        if fid and not (
            place
            and can_read_facility_rows(user, state=place[0], district=place[1], facility_id=fid)
        ):
            e = {**e, "data": {"withheld": True}}
        out.append(e)
    return out


@router.get("/events", response_model=EventsOut, tags=["realtime"])
async def poll_events(
    after: int | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> EventsOut:
    out = await events.since(session, after)
    ids = sorted({
        (e.get("data") or {}).get("facility_id") for e in out["events"]
    } - {None})
    where: dict[str, tuple[str, str]] = {}
    if ids:
        # Bounded by the batch (events.MAX_BATCH).
        rows = await session.execute(
            select(Facility.id, Facility.state_silo, Facility.district).where(Facility.id.in_(ids))
        )
        where = {fid: (state, district) for fid, state, district in rows.all()}
    return EventsOut.model_validate({**out, "events": scope_events(out["events"], where, user)})


# ======================================================= medicine movements ===
# The two-sided ledger (spec 26.3): what the warehouse says it sent, against
# what the facility confirms it received.


class MovementOut(BaseModel):
    id: int
    batch_id: str
    sku_code: str
    sku_name: str
    unit: str
    from_ref: str
    to_facility: str
    facility_name: str
    state_silo: str
    district: str
    qty_dispatched: float
    dispatched_at: datetime
    dispatch_source: str
    transfer_id: int | None
    expected_by: datetime
    qty_received: float | None
    received_at: datetime | None
    received_via: str | None
    status: str
    discrepancy_qty: float | None
    days_outstanding: float | None
    note: str | None


class MovementsOut(BaseModel):
    view: str
    counts: dict
    short_units: float
    movements: list[MovementOut]
    # Set when the totals are shown without their rows (fix #77).
    rows_withheld: str | None = None


@router.get("/movements", response_model=MovementsOut, tags=["movements"])
async def list_movements(
    state: str | None = None,
    district: str | None = None,
    facility: str | None = None,
    sku: str | None = None,
    view: str = Query(default="attention"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> MovementsOut:
    # A facility user sees their own facility's deliveries, a block MO their
    # block's: the scope is narrowed here rather than trusted from the query.
    if user.role == "facility_user":
        facility, district, state = user.facility_id, None, None
    elif user.role == "block_mo":
        state, district = user.state_silo, user.district
    elif user.role == "state_officer":
        state = user.state_silo

    # Fix #77: a consignment is a row about one centre. The national role
    # reads the ledger's totals — an aggregate — and none of its rows.
    withheld = None
    rows: list = []
    totals_scope = dict(state=state, district=district, facility_id=facility, sku=sku)
    if user.role == "admin":
        narrowed = _narrow_to_rows_scope(user, state, district)
        if narrowed is None:
            withheld = held_message(user, state) if state else (
                "Held in each state's store — the national view sees totals only."
            )
        else:
            rows = await movements.list_movements(
                session, state=narrowed[0], district=narrowed[1], facility_id=facility,
                sku=sku, view=view, limit=limit, offset=offset,
            )
    else:
        rows = await movements.list_movements(
            session, state=state, district=district, facility_id=facility,
            sku=sku, view=view, limit=limit, offset=offset,
        )
    totals = await movements.summary(session, **totals_scope)
    return MovementsOut(
        view=view,
        counts=totals["counts"],
        short_units=totals["short_units"],
        movements=[MovementOut(**asdict(r)) for r in rows],
        rows_withheld=withheld,
    )


class ReceiptIn(BaseModel):
    qty_received: float = Field(ge=0)
    note: str | None = None
    via: str = "form"


class ReceiptOut(BaseModel):
    movement: MovementOut
    status_before: str
    status_after: str
    status_changed: bool
    qty_on_hand: float


@router.post(
    "/movements/{movement_id}/receipt", response_model=ReceiptOut, tags=["movements"]
)
async def confirm_receipt(
    movement_id: int,
    payload: ReceiptIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> ReceiptOut:
    movement = await session.get(MedicineMovement, movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="Unknown movement")
    facility = await session.get(Facility, movement.to_facility)
    if facility is None:
        raise HTTPException(status_code=404, detail="Unknown facility")
    # Confirming a delivery is reporting a fact about your own facility, so it
    # carries the same permission as submitting a stock reading: the receiving
    # centre's, never the dispatching side's or an officer's (fix list #74).
    _require_facts(user, facility)
    # Only the web form comes through this endpoint; phone channels confirm
    # through their own webhook adapters, which set their own `via`.
    if payload.via not in WEB_SOURCES and not (
        settings.demo_mode and payload.via in DEMO_CHANNEL_SOURCES
    ):
        raise HTTPException(
            status_code=422, detail=f"Channel '{payload.via}' cannot confirm through this endpoint"
        )

    try:
        row, effect = await movements.confirm_receipt(
            session, movement_id,
            qty_received=payload.qty_received,
            via=payload.via,
            by_ref=f"user:{user.id}",
            note=payload.note,
        )
    except movements.ReceiptError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return ReceiptOut(movement=MovementOut(**asdict(row)), **effect)


# ===================================================================== chase ===
# Fix list #74. Officers no longer confirm a centre's deliveries, check its
# staff in or send its bed report; they chase the centre for them instead.


def chase_text(
    topic: str, facility_name: str, *, batch: str | None = None, medicine: str | None = None
) -> str:
    """The reminder, naming what to send back in the SMS grammar the ingestion
    spine reads (GOT, IN/OUT, BEDS). A delivery reminder never states the
    dispatched quantity: the centre counts first, then reports what it
    counted."""
    if topic == "receipt":
        return (
            "SwasthSetu reminder for {0}: batch {1} of {2} was sent to you. When it "
            "arrives, count it and reply GOT {1} followed by the number you counted."
        ).format(facility_name, batch, medicine)
    if topic == "checkin":
        return (
            "SwasthSetu reminder for {0}: no staff check-in has been recorded today. "
            "Staff on duty, reply IN when you arrive and OUT when you leave."
        ).format(facility_name)
    return (
        "SwasthSetu reminder for {0}: today's bed report is due. Send a ward photo "
        "with today's code, or reply BEDS followed by the number of occupied beds."
    ).format(facility_name)


class ChaseIn(BaseModel):
    topic: Literal["receipt", "checkin", "beds"]
    movement_id: int | None = None


class ChaseOut(BaseModel):
    facility_id: str
    sent_to: str
    channel: str
    body: str
    # Always "simulated". This system keeps only a salted hash of each
    # handset's number, so it has nothing to text a real phone with; the
    # reminder goes to the channel simulator's outbound log, and says so.
    status: str


@router.post("/facilities/{facility_id}/chase", response_model=ChaseOut, tags=["field"])
async def chase_facility(
    facility_id: str,
    payload: ChaseIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> ChaseOut:
    """Remind a centre to report what only it can report."""
    if user.role == "facility_user":
        raise HTTPException(
            status_code=403,
            detail="A centre reports for itself; chasing is for the officers who oversee it.",
        )
    facility = await _facility_in_scope(session, facility_id, user)
    _require_demo_write(user, facility)

    batch = medicine = None
    if payload.topic == "receipt":
        if payload.movement_id is None:
            raise HTTPException(status_code=422, detail="Say which delivery to chase")
        movement = await session.get(MedicineMovement, payload.movement_id)
        if movement is None or movement.to_facility != facility.id:
            raise HTTPException(status_code=404, detail="No such delivery to this centre")
        if movement.status != movements.OPEN:
            raise HTTPException(status_code=409, detail="That delivery has already been confirmed")
        sku = await session.get(Sku, movement.sku_code)
        batch, medicine = movement.batch_id, sku.name if sku else movement.sku_code

    # The reporting handset first ('reporter' sorts before 'supervisor').
    contact = await session.scalar(
        select(FacilityContact)
        .where(FacilityContact.facility_id == facility.id, FacilityContact.is_active.is_(True))
        .order_by(FacilityContact.role)
        .limit(1)
    )
    if contact is None:
        raise HTTPException(status_code=409, detail="This centre has no registered handset to remind")

    body = chase_text(payload.topic, facility.name, batch=batch, medicine=medicine)
    await comms.record_outbound(
        session, channel="sms", to_ref=contact.masked, body=body,
        provider_sid=None, status="simulated",
    )
    await session.commit()
    return ChaseOut(
        facility_id=facility.id, sent_to=contact.masked, channel="sms", body=body,
        status="simulated",
    )


# =============================================================== bed capture ===
# Spec 26.2: a ward photo carrying the day's rotating code, read by Gemini,
# checked against the facility's registered location and its admission
# register. Every check that ran is reported alongside its result.


class BedCodeOut(BaseModel):
    facility_id: str
    for_date: date
    code: str
    delivered_at: datetime | None
    # How the code reaches a facility in the field. Shown so nobody mistakes
    # the demo's on-screen code for the delivery mechanism.
    delivery: str = "Sent by SMS and read out on the IVR call each morning"


@router.get("/facilities/{facility_id}/bed-code", response_model=BedCodeOut, tags=["beds"])
async def bed_code(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> BedCodeOut:
    # Reading the day's code is viewing, not reporting: an officer may see it
    # for any centre they oversee (fix list #74 narrowed only the reports).
    facility = await _facility_in_scope(session, facility_id, user)
    code = await beds.code_for(session, facility.id)
    await session.commit()
    return BedCodeOut(
        facility_id=facility.id,
        for_date=code.for_date,
        code=code.code,
        delivered_at=code.delivered_at,
    )


class LocationIn(BaseModel):
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    # What produced this location. Recorded as given and never upgraded: a
    # simulated signal must not be displayed as a verified one (spec 26.1).
    method: str = "none"


class BedReportIn(BaseModel):
    ward: str = "general"
    source: str = "photo"
    location: LocationIn = Field(default_factory=LocationIn)
    register_admissions: int | None = Field(default=None, ge=0)
    # A ward photograph, base64. Required when the live model is in use.
    image_base64: str | None = None
    image_mime: str = "image/jpeg"
    # Demo only: stands in for what the camera would have captured, so the
    # stale-code and wrong-place paths can be shown deliberately.
    simulate: dict | None = None


class BedReportOut(BaseModel):
    id: int
    facility_id: str
    ward: str
    beds_total: int | None
    beds_occupied: int | None
    reported_at: datetime
    source: str
    verification: str
    code_ok: bool | None
    code_read: str | None
    geofence_ok: bool | None
    geofence_km: float | None
    loc_method: str | None
    register_admissions: int | None
    model_confidence: float | None
    model: str | None
    # Who produced the numbers, in plain words: seeded, typed, or which model.
    read_by: str
    reasons: list[str]


def _bed_report_out(report) -> BedReportOut:
    payload = report.raw_payload or {}
    return BedReportOut(
        id=report.id,
        facility_id=report.facility_id,
        ward=report.ward,
        beds_total=report.beds_total,
        beds_occupied=report.beds_occupied,
        reported_at=report.reported_at,
        source=report.source,
        verification=report.verification,
        code_ok=report.code_ok,
        code_read=report.code_read,
        geofence_ok=report.geofence_ok,
        geofence_km=report.geofence_km,
        loc_method=report.loc_method,
        register_admissions=report.register_admissions,
        model_confidence=report.model_confidence,
        model=payload.get("model"),
        read_by=beds.read_by(
            model=payload.get("model"), notes=payload.get("notes"), source=report.source
        ),
        reasons=payload.get("reasons", []),
    )


async def _facility_for_report(
    session: AsyncSession, facility_id: str, user: Principal
) -> Facility:
    """The facility a bed report or check-in is being made for: by the centre
    itself only (auth.can_report_facts)."""
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Unknown facility")
    _require_facts(user, facility)
    return facility


@router.post(
    "/facilities/{facility_id}/bed-reports", response_model=BedReportOut, tags=["beds"]
)
async def submit_bed_report(
    facility_id: str,
    payload: BedReportIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> BedReportOut:
    facility = await _facility_for_report(session, facility_id, user)
    if payload.location.method not in LOC_METHODS:
        raise HTTPException(status_code=422, detail="Unknown location method")
    if payload.simulate is not None and not (
        settings.demo_mode and settings.llm_mode != "live"
    ):
        raise HTTPException(
            status_code=422,
            detail="Simulated extractions are only accepted in demo mode with the mock model",
        )

    image: bytes | None = None
    if payload.image_base64:
        try:
            image = base64.b64decode(payload.image_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=422, detail="Photo is not valid base64") from exc
        if len(image) > vision.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Photo is too large")

    try:
        extraction = await vision.read_ward_photo(
            image, payload.image_mime, simulate=payload.simulate
        )
    except vision.VisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    report, _ = await beds.record_report(
        session,
        facility,
        extraction,
        ward=payload.ward,
        source=payload.source,
        loc_method=payload.location.method,
        loc_lat=payload.location.lat,
        loc_lng=payload.location.lng,
        loc_accuracy_m=payload.location.accuracy_m,
        register_admissions=payload.register_admissions,
        media_ref=beds.hash_media(image) if image else None,
    )
    await session.commit()
    return _bed_report_out(report)


@router.get(
    "/facilities/{facility_id}/bed-reports",
    response_model=list[BedReportOut],
    tags=["beds"],
)
async def list_bed_reports(
    facility_id: str,
    limit: int = Query(default=8, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[BedReportOut]:
    await _readable_facility(session, facility_id, user)
    return [_bed_report_out(r) for r in await beds.recent_reports(session, facility_id, limit)]


# ================================================================ attendance ===
# Spec 26.1: a shift start or end, carrying whatever location the channel can
# actually supply — and saying so when it can supply none.


class CheckinIn(BaseModel):
    staff_ref: str = Field(min_length=1, max_length=64)
    action: str = "in"
    shift: str | None = None
    source: str = "form"
    location: LocationIn = Field(default_factory=LocationIn)
    # Supplied only by a USSD gateway or operator integration; an inbound
    # Twilio call never carries it (spec 26.1).
    cell_id: str | None = None


class CheckinOut(BaseModel):
    facility_id: str
    action: str
    shift: str | None
    source: str | None
    loc_method: str | None
    geofence_km: float | None
    geofence_ok: bool | None
    checked_in_at: datetime
    checked_out_at: datetime | None


class AttendanceOut(BaseModel):
    facility_id: str
    roster: int
    present: int
    rate: float | None
    by_method: dict[str, int]
    geofence_pass: int
    geofence_checked: int
    footfall_today: int | None
    contradiction: str | None
    last_checkin_at: datetime | None = None


@router.get(
    "/facilities/{facility_id}/attendance", response_model=AttendanceOut, tags=["attendance"]
)
async def facility_attendance(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> AttendanceOut:
    await _readable_facility(session, facility_id, user)
    return AttendanceOut(**asdict(await attendance.summarise(session, facility_id)))


# ---------------------------------------------------- one's own record ---
# The only endpoint in this file that returns a per-person attendance history,
# and it returns exactly one person's: the reader's.
#
# The scoping is structural, not a check that could be forgotten. There is no
# `staff_ref` path parameter, no query parameter and no request body — the
# reference comes off the signed-in Principal and from nowhere else, so there
# is no value an attacker can vary to reach somebody else's rows. An officer
# calling this gets their own record or nothing; the facility-level endpoint
# above is still the only view of other people, and it returns counts with no
# reference in them at all (rule 8, v3 1.8).


class VerificationPingOut(BaseModel):
    sent_at: datetime
    responded_at: datetime | None
    channel: str
    loc_method: str | None
    cell_id: str | None
    geofence_km: float | None
    geofence_ok: bool | None
    outcome: str


class SelfDayOut(BaseModel):
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
    pings: list[VerificationPingOut]
    synthetic: bool = False


class SelfRecordOut(BaseModel):
    facility_id: str
    facility_name: str
    window_days: int
    days_present: int
    days_absent: int
    pings_sent: int
    pings_confirmed: int
    pings_unanswered: int
    days: list[SelfDayOut]


@router.get("/me/attendance", response_model=SelfRecordOut, tags=["attendance"])
async def my_attendance(
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> SelfRecordOut:
    """This account's own attendance record. Never anybody else's."""
    if not user.facility_id or (not user.staff_ref and not settings.demo_mode):
        raise HTTPException(
            status_code=404,
            detail="This account is not linked to an attendance record",
        )
    facility = await session.get(Facility, user.facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Unknown facility")

    if user.staff_ref is None:
        # Demo mode only (checked above): a synthetic, labelled record.
        record = attendance.synthetic_record(user.facility_id)
    else:
        record = await attendance.own_record(session, user.facility_id, user.staff_ref)
        if settings.demo_mode:
            record = attendance.with_synthetic_today(record)
    payload = asdict(record)
    # The pseudonymous reference is how the rows were found; it is not part of
    # the answer, and the reader already knows who they are.
    payload.pop("staff_ref", None)
    return SelfRecordOut(facility_name=facility.name, **payload)


@router.post(
    "/facilities/{facility_id}/checkins", response_model=CheckinOut, tags=["attendance"]
)
async def submit_checkin(
    facility_id: str,
    payload: CheckinIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> CheckinOut:
    facility = await _facility_for_report(session, facility_id, user)
    if payload.action not in ("in", "out"):
        raise HTTPException(status_code=422, detail="Action must be 'in' or 'out'")
    if payload.location.method not in LOC_METHODS:
        raise HTTPException(status_code=422, detail="Unknown location method")
    if payload.shift is not None and payload.shift not in attendance.SHIFTS:
        raise HTTPException(status_code=422, detail="Unknown shift")

    row = await attendance.record_checkin(
        session,
        facility,
        staff_ref=payload.staff_ref,
        action=payload.action,
        shift=payload.shift,
        source=payload.source,
        lat=payload.location.lat,
        lng=payload.location.lng,
        loc_method=payload.location.method,
        cell_id=payload.cell_id,
    )
    await session.commit()
    return CheckinOut(
        facility_id=row.facility_id,
        action=payload.action,
        shift=row.shift,
        source=row.source,
        loc_method=row.loc_method,
        geofence_km=row.geofence_km,
        geofence_ok=row.geofence_ok,
        checked_in_at=row.checked_in_at,
        checked_out_at=row.checked_out_at,
    )


# ============================================================== trust layer ===
# Spec 12.6. A score is only ever an invitation to look, so every response
# carries the sentences behind it — a number an officer cannot interrogate is
# a number they are right to ignore.


class TrustComponentOut(BaseModel):
    signal: str
    penalty: float
    weight: float
    cost: float
    reason: str
    # Where to open the rows this sentence came from. The movement tab reads
    # the same filter, so the link and the score are one query.
    evidence: dict | None = None
    # Fix #60: how many observations the sentence rests on, in words, and
    # whether there were enough to score it at all.
    sample: int | None = None
    basis: str | None = None
    scored: bool = True


class TrustOut(BaseModel):
    facility_id: str
    score: float
    band: str
    components: list[TrustComponentOut]
    computed_at: datetime
    # What the score actually does: warn this facility earlier (spec 12.6).
    warning_multiplier: float


class AuditRowOut(TrustOut):
    facility_name: str
    type: str
    district: str
    state_silo: str
    lat: float
    lng: float


@router.get("/trust/queue", response_model=list[AuditRowOut], tags=["trust"])
async def audit_queue(
    state: str | None = None,
    district: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[AuditRowOut]:
    # Narrowed to the officer's own patch: an audit list is about where to send
    # someone, and nobody sends inspectors outside their jurisdiction.
    if user.role == "facility_user":
        raise HTTPException(
            status_code=403, detail="The audit queue is for district and state officers"
        )
    if user.role == "block_mo":
        state, district = user.state_silo, user.district
    elif user.role == "state_officer":
        state = user.state_silo
    else:
        # Fix #77: the queue is a list of named centres with their evidence.
        narrowed = _narrow_to_rows_scope(user, state, district)
        if narrowed is None:
            raise HTTPException(
                status_code=403,
                detail=held_message(user, state) if state else (
                    "Held in each state's store — the national view sees district summaries only."
                ),
            )
        state, district = narrowed
    if not state and not district:
        # Scoring the whole country live measured 46s (docs/STORAGE_NOTES.md),
        # and the panel's live refresh piled those requests on each other.
        raise HTTPException(
            status_code=400,
            detail="Choose a state first: the audit queue is scored live, one state at a time.",
        )

    rows = await trust.audit_queue(session, state=state, district=district, limit=limit)
    return [
        AuditRowOut(
            **{k: v for k, v in row.items() if k != "components"},
            components=[TrustComponentOut(**c) for c in row["components"]],
            warning_multiplier=trust.warning_multiplier(row["score"]),
        )
        for row in rows
    ]


class TrustQueryOut(BaseModel):
    """What the trust score was computed from, for the panel's footnote."""

    window_days: int
    computed_at: datetime


@router.get("/facilities/{facility_id}/trust", response_model=TrustOut | None, tags=["trust"])
async def facility_trust(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> TrustOut | None:
    # Computed here and now from the ledger, the ward photos and the
    # check-ins — never read back from a stored column, so the panel cannot
    # show a number that the rows beneath it have already moved past.
    await _readable_facility(session, facility_id, user)
    score = await trust.for_facility(session, facility_id)
    if score is None:
        return None
    return TrustOut(
        facility_id=score.facility_id,
        score=score.score,
        band=score.band,
        components=[TrustComponentOut(**c) for c in score.as_rows()],
        computed_at=datetime.now(timezone.utc),
        warning_multiplier=trust.warning_multiplier(score.score),
    )


@router.post(
    "/facilities/{facility_id}/trust/explain", response_model=ExplanationOut, tags=["trust"]
)
async def explain_facility_trust(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> ExplanationOut:
    """How this facility's disagreeing signals relate, as a reason to look.

    The rules computed the score; the model only words how the flagged
    sentences fit together, about the facility and never a person (12.6).
    """
    facility = await _facility_in_scope(session, facility_id, user)
    score = await trust.for_facility(session, facility.id)
    if score is None:
        raise HTTPException(status_code=404, detail="Nothing has been reported here to score yet")
    rows = trust.why_rows(score)
    fallback = trust.rules_why(score)
    if not rows:
        return _rules_explanation(fallback)
    return await _explain_or_rules(
        lambda: vision.explain_trust(
            facility_name=facility.name, score=round(score.score * 100), rows=rows
        ),
        fallback,
    )


# ==================================================== the silo inspector ===
# Spec 12.2 and 27. The Federation page shows two things beside each other:
# the accuracy the shared model reached, and the evidence for what crossed the
# wire to reach it. The second is the point — an accuracy chart alone asks to
# be believed, while measured bytes, tensor shapes, a weight hash and an
# asserted zero can be argued with.


class FederationSiloOut(BaseModel):
    state: str
    windows: int
    counts_as: int
    trust: float
    flagged_pct: float
    train_loss: float


class FederationRoundOut(BaseModel):
    round_no: int
    global_val_mae: float | None
    baseline_mae: float | None
    silos_reporting: int | None
    bytes_transmitted: int | None
    tensor_count: int
    weights_sha256: str | None
    raw_rows_transmitted: int
    completed_at: datetime
    per_silo: list[FederationSiloOut]


class FederationOut(BaseModel):
    available: bool
    run_id: str | None = None
    strategy: str | None = None
    rounds: list[FederationRoundOut] = Field(default_factory=list)
    first_mae: float | None = None
    best_mae: float | None = None
    baseline_mae: float | None = None
    improvement_pct: float | None = None
    bytes_per_round: int | None = None
    total_bytes: int | None = None
    raw_rows_transmitted: int = 0
    tensor_shapes: dict[str, list[int]] = Field(default_factory=dict)
    note: str | None = None


@router.get("/federation/inspector", response_model=FederationOut, tags=["federation"])
async def federation_inspector(
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> FederationOut:
    latest = await session.scalar(
        select(FederationRound.run_id).order_by(FederationRound.completed_at.desc()).limit(1)
    )
    if latest is None:
        return FederationOut(
            available=False,
            note=(
                "No federated run has been recorded. Start the SuperLink and the four "
                "SuperNodes, then run `flwr run . local-deployment` in backend/federation."
            ),
        )

    rows = list(
        (
            await session.execute(
                select(FederationRound)
                .where(FederationRound.run_id == latest)
                .order_by(FederationRound.round_no)
            )
        )
        .scalars()
        .all()
    )

    rounds = [
        FederationRoundOut(
            round_no=r.round_no,
            global_val_mae=r.global_val_mae,
            baseline_mae=r.baseline_mae,
            silos_reporting=r.silos_reporting,
            bytes_transmitted=r.bytes_transmitted,
            tensor_count=len(r.tensor_shapes or {}),
            weights_sha256=r.weights_sha256,
            raw_rows_transmitted=r.raw_rows_transmitted,
            completed_at=r.completed_at,
            per_silo=[
                FederationSiloOut(state=state, **values)
                for state, values in sorted((r.per_silo or {}).items())
            ],
        )
        for r in rows
    ]
    scored = [r.global_val_mae for r in rows if r.global_val_mae is not None]
    baseline = next((r.baseline_mae for r in reversed(rows) if r.baseline_mae), None)
    best = min(scored) if scored else None
    return FederationOut(
        available=True,
        run_id=latest,
        strategy=next((r.strategy for r in reversed(rows) if r.strategy), None),
        rounds=rounds,
        first_mae=scored[0] if scored else None,
        best_mae=best,
        baseline_mae=baseline,
        improvement_pct=(
            round((baseline - best) / baseline * 100, 1) if baseline and best else None
        ),
        bytes_per_round=max((r.bytes_transmitted or 0) for r in rows) if rows else None,
        total_bytes=sum((r.bytes_transmitted or 0) for r in rows),
        # Asserted by the aggregator before each round was written, not typed
        # in here: see federation/pytorchexample/inspector.py.
        raw_rows_transmitted=sum(r.raw_rows_transmitted for r in rows),
        tensor_shapes=next((r.tensor_shapes for r in reversed(rows) if r.tensor_shapes), {}),
    )


class LivePhaseOut(BaseModel):
    label: str
    at_s: float


class LiveRoundOut(BaseModel):
    run_id: str
    round_no: int
    status: str  # running | done | failed
    started_at: datetime
    finished_at: datetime | None
    phases: list[LivePhaseOut]
    error: str | None


class LiveRoundStateOut(BaseModel):
    """Whether one more real round can be started from here, and why not."""

    available: bool
    reason: str | None
    job: LiveRoundOut | None


def _live_out(job: federation_live.LiveRound | None) -> LiveRoundOut | None:
    if job is None:
        return None
    return LiveRoundOut(
        run_id=job.run_id, round_no=job.round_no, status=job.status,
        started_at=job.started_at, finished_at=job.finished_at,
        phases=[LivePhaseOut(label=label, at_s=at) for label, at in job.phases],
        error=job.error,
    )


@router.get("/federation/live", response_model=LiveRoundStateOut, tags=["federation"])
async def federation_live_state() -> LiveRoundStateOut:
    reason = await federation_live.unavailable_reason()
    return LiveRoundStateOut(
        available=reason is None, reason=reason, job=_live_out(federation_live.current())
    )


@router.post(
    "/federation/live", response_model=LiveRoundOut, status_code=202, tags=["federation"]
)
async def federation_live_start(
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> LiveRoundOut:
    """Continue the latest recorded run by one real round (local only).

    The web service starts `flwr run` and reads its output; the training, the
    hash check on resume and the new row all happen in the ServerApp.
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only an administrator can start a training round")
    latest = await session.scalar(
        select(FederationRound.run_id).order_by(FederationRound.completed_at.desc()).limit(1)
    )
    if latest is None:
        raise HTTPException(status_code=409, detail="There is no recorded run to continue")
    last_round = await session.scalar(
        select(func.max(FederationRound.round_no)).where(FederationRound.run_id == latest)
    )
    try:
        job = await federation_live.start(run_id=latest, last_round=int(last_round or 0))
    except federation_live.AlreadyRunning:
        raise HTTPException(status_code=409, detail="A round is already running") from None
    except federation_live.Unavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _live_out(job)  # type: ignore[return-value]


class OutbreakOut(BaseModel):
    unique_id: str
    year: int
    week: int
    state: str
    state_code: str | None
    district: str
    disease: str
    cases: int
    deaths: int
    start_date: str | None
    reported_date: str | None
    status: str | None
    in_network: bool
    # Fix #59: what makes a report row a warning.
    facilities: int = 0
    medicines: list[str] = []
    age_days: int | None = None
    historical: bool = True
    active: bool = False


class OutbreaksOut(BaseModel):
    source: str
    source_url: str
    columns: list[str]
    reports: list[dict]
    rows: list[OutbreakOut]
    # A row older than this many days is historical, not a current warning.
    ttl_days: int = 14


class NextOutbreakOut(BaseModel):
    disease: str
    basis: str
    without_on: date


class NextPairOut(BaseModel):
    """One district × medicine pair projected to run short (fix #45)."""

    state: str
    state_name: str
    district: str
    sku_code: str
    sku_name: str
    centres: int
    first_on: date
    # None where the reader may not read that centre's rows (fix #77).
    first_centre: str | None
    by_forecast: int
    # Which rule the dates rest on: forecast | mixed | burn_rate | outbreak.
    source: str
    outbreak: NextOutbreakOut | None
    line: str


class NextWarningsOut(BaseModel):
    horizon_days: int
    as_of: datetime
    pairs: list[NextPairOut]
    counts_overdue: int
    forecast_published_at: datetime | None
    forecast_max_age_days: float


@router.get("/warnings/next", response_model=NextWarningsOut, tags=["warnings"])
async def next_warnings(
    state: str | None = Query(default=None, pattern="^[A-Z]{2}$"),
    source: Literal["all", "forecast"] = "all",
    limit: int = Query(default=earlywarning.DEFAULT_LIMIT, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> NextWarningsOut:
    """The "Next 14 days" strip: district × medicine pairs whose centres are
    projected to run out inside the horizon, earliest first. `source=forecast`
    keeps only dates that rest on the shared model's fresh forecast — the same
    data as the Federation tab shows it. District aggregates only; one capped
    aggregate over the map's own table."""
    out = await earlywarning.strip(
        session, datetime.now(timezone.utc),
        state=state, only_forecast=source == "forecast", limit=limit,
    )
    # The district figures are an aggregate; the first centre's name is a row.
    named = {
        p.key: can_read_facility_rows(user, state=p.state, district=p.district)
        for p in out.pairs
    }
    return NextWarningsOut(
        horizon_days=out.horizon_days,
        as_of=out.as_of,
        counts_overdue=out.counts_overdue,
        forecast_published_at=out.forecast_published_at,
        forecast_max_age_days=out.forecast_max_age_days,
        pairs=[
            NextPairOut(
                state=p.state, state_name=earlywarning.state_name(p.state),
                district=p.district, sku_code=p.sku_code, sku_name=p.sku_name,
                centres=p.centres, first_on=p.first_on,
                first_centre=p.first_centre if named[p.key] else None,
                by_forecast=p.by_forecast, source=p.source,
                outbreak=NextOutbreakOut(**vars(p.outbreak)) if p.outbreak else None,
                line=earlywarning.line(p, named=named[p.key]),
            )
            for p in out.pairs
        ],
    )


async def _district_facility_counts(session: AsyncSession) -> dict[tuple[str, str], int]:
    """Centres per (state, district): one grouped read of the facility list."""
    rows = await session.execute(
        select(Facility.state_silo, Facility.district, func.count()).group_by(
            Facility.state_silo, Facility.district
        )
    )
    return {(s, d): n for s, d, n in rows.all()}


@router.get("/outbreaks", response_model=OutbreaksOut, tags=["outbreaks"])
async def outbreaks(
    state: str | None = None, session: AsyncSession = Depends(get_session)
) -> OutbreaksOut:
    """Outbreaks from the IDSP Weekly Outbreak Report, parsed once from the
    published PDFs into a committed file — each row with its age, the network's
    centres in its district, the medicines its disease drives and whether an
    outbreak is active there (fix #59). Rows inside the outbreak window come
    first; older ones are historical."""
    data = idsp.load()
    now = datetime.now(timezone.utc)
    active = {
        (o.state_silo, (o.district or "").lower()) for o in await outbreak.active(session, now, state)
    }
    rows = idsp.panel_rows(
        idsp.outbreaks(state),
        ours=idsp.network_districts(),
        facility_counts=await _district_facility_counts(session),
        today=workspace.in_india(now).date(),
        ttl_days=settings.outbreak_ttl_days,
        active=active,
    )
    return OutbreaksOut(
        source=data["source"], source_url=data["source_url"], columns=data["columns"],
        reports=data["reports"], ttl_days=settings.outbreak_ttl_days,
        rows=[OutbreakOut(**r) for r in rows],
    )


class StockingAdviceOut(BaseModel):
    unique_id: str
    district: str
    state_code: str
    disease: str
    medicines: list[str]
    signals: list[str]
    action: str


@router.get("/outbreaks/advice", response_model=list[StockingAdviceOut], tags=["outbreaks"])
async def outbreak_advice(state: str | None = None) -> list[StockingAdviceOut]:
    """Stocking advice from IDSP outbreaks and the monsoon calendar. How much
    demand rises is not guessed here; declaring the outbreak measures it (#41).
    Computed on request; nothing is stored."""
    month = datetime.now(timezone.utc).month
    return [StockingAdviceOut(**a) for a in idsp.stocking_advice(state, month)]



# ================================================= active outbreaks (#41) ===
# Spec v3 §12.5: an outbreak is a temporary multiplier into the §12.3 solver,
# not a new subsystem. Declaring one re-plans exactly the medicines its
# disease drives — single-medicine plans through the same solver and the same
# approval gate — and ending it re-plans them without the surge.


class OutbreakMedicineOut(BaseModel):
    sku_code: str
    sku_name: str
    observed_ratio: float | None
    multiplier: float | None
    basis: str | None
    detail: str


class OutbreakWarningOut(BaseModel):
    outbreak_id: int
    facility_id: str
    facility_name: str
    district: str
    sku_code: str
    sku_name: str
    runs_out_on: date
    runs_out_without: date
    basis: str
    line: str


class ActiveOutbreakOut(BaseModel):
    id: int
    state: str
    district: str
    disease: str
    source: str
    source_ref: str | None
    surge_pct: float | None
    declared_by: str | None
    declared_at: datetime | None
    expires_at: datetime | None
    facilities: int
    # Centres whose shelf would already be empty at the surge rate: count them.
    count_overdue: int
    # Where the district sits, so the map can ring it (fix #59).
    lat: float | None = None
    lng: float | None = None
    # How many centres run out inside the horizon; the list below names them
    # only for a reader who may read that district's rows (fix #77).
    warnings_count: int = 0
    medicines: list[OutbreakMedicineOut]
    warnings: list[OutbreakWarningOut]


class ActiveOutbreaksOut(BaseModel):
    ttl_days: int
    window_days: int
    min_rise_pct: int
    max_surge_pct: float
    diseases: list[str]
    outbreaks: list[ActiveOutbreakOut]


class DeclareOutbreakIn(BaseModel):
    state: str
    district: str
    disease: str
    # The officer's expected surge, used only when the readings show no rise,
    # and shown as their assumption wherever it is used.
    surge_pct: float | None = Field(default=None, ge=0, le=settings.outbreak_max_surge_pct)


class DeclaredOutbreakOut(BaseModel):
    outbreak: ActiveOutbreakOut
    trips_proposed: int
    pre_positioning_trips: int


def _active_out(view, warnings: list[dict], named: bool = True) -> ActiveOutbreakOut:
    row = view.row
    at = geo.district_anchor(row.state_silo or "", row.district or "")
    count = len(warnings)
    if not named:
        warnings = []
    return ActiveOutbreakOut(
        warnings_count=count,
        lat=at[0] if at else None,
        lng=at[1] if at else None,
        id=row.id,
        state=row.state_silo or "",
        district=row.district or "",
        disease=row.disease_category or "",
        source=row.source or "officer",
        source_ref=row.source_ref,
        surge_pct=float(row.surge_pct) if row.surge_pct is not None else None,
        declared_by=row.declared_by,
        declared_at=row.triggered_at,
        expires_at=row.expires_at,
        facilities=len(view.ids),
        count_overdue=view.overdue,
        medicines=[OutbreakMedicineOut(**m) for m in view.medicines],
        warnings=[OutbreakWarningOut(**w) for w in warnings],
    )


@router.get("/outbreaks/active", response_model=ActiveOutbreaksOut, tags=["outbreaks"])
async def active_outbreaks(
    state: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> ActiveOutbreaksOut:
    """Active outbreaks with their surge per medicine and the centres that run
    out inside the horizon at the surge rate. Each outbreak's reads are bounded
    by its district's facility ids."""
    now = datetime.now(timezone.utc)
    out: list[ActiveOutbreakOut] = []
    for row in await outbreak.active(session, now, state):
        view = await outbreak.evaluate(session, row, now)
        out.append(
            _active_out(
                view,
                await outbreak.warnings(session, view, now),
                named=can_read_facility_rows(
                    user, state=row.state_silo or "", district=row.district or ""
                ),
            )
        )
    return ActiveOutbreaksOut(
        ttl_days=settings.outbreak_ttl_days,
        window_days=settings.outbreak_window_days,
        min_rise_pct=round(settings.outbreak_min_rise * 100),
        max_surge_pct=settings.outbreak_max_surge_pct,
        diseases=sorted(idsp.DISEASE_MEDICINES),
        outbreaks=out,
    )


async def _open_outbreak(
    session: AsyncSession, state: str, district: str, disease: str, now: datetime
) -> OutbreakEvent | None:
    return await session.scalar(
        select(OutbreakEvent).where(
            OutbreakEvent.state_silo == state,
            OutbreakEvent.district == district,
            OutbreakEvent.disease_category == disease,
            OutbreakEvent.ended_at.is_(None),
            OutbreakEvent.expires_at > now,
        )
    )


async def _replan_medicines(session: AsyncSession, state: str, skus: list[str]) -> list[int]:
    """Single-medicine plans, one per medicine, each recorded like any plan."""
    ids: list[int] = []
    for sku in skus:
        result = await redistribution.generate_plan(session, state, sku)
        ids.extend(result.transfer_ids)
        await events.record(
            session,
            events.TRANSFER_PROPOSED,
            {
                "state": state,
                "sku": sku,
                "transfers": len(result.transfer_ids),
                "replaced": result.replaced_ids,
                "triggered_by": "outbreak",
            },
            state_silo=state,
        )
    await session.commit()
    return ids


@router.post("/outbreaks/declare", response_model=DeclaredOutbreakOut, tags=["outbreaks"])
async def declare_outbreak(
    payload: DeclareOutbreakIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> DeclaredOutbreakOut:
    refusal = outbreak.may_declare(user, payload.state, payload.district)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    skus = outbreak.medicines_for(payload.disease)
    if not skus:
        raise HTTPException(
            status_code=422,
            detail=(
                "That disease is not in the disease-to-medicine map, so nothing can be "
                "pre-positioned for it"
            ),
        )
    if not await outbreak.district_ids(session, payload.state, payload.district):
        raise HTTPException(status_code=404, detail="No centres in that district")

    now = datetime.now(timezone.utc)
    row = await _open_outbreak(session, payload.state, payload.district, payload.disease, now)
    if row is None:
        row = OutbreakEvent(
            state_silo=payload.state,
            district=payload.district,
            disease_category=payload.disease,
            source="officer",
        )
        session.add(row)
    # Declaring again refreshes the expectation and the clock; it never adds a
    # second copy of the same outbreak.
    row.surge_pct = payload.surge_pct
    row.declared_by = user.name
    row.expires_at = now + timedelta(days=settings.outbreak_ttl_days)
    row.triggered_at = now
    await session.flush()
    await events.record(
        session,
        events.OUTBREAK_DECLARED,
        {
            "outbreak_id": row.id,
            "state": payload.state,
            "district": payload.district,
            "disease": payload.disease,
            "surge_pct": payload.surge_pct,
            "declared_by": user.name,
        },
        state_silo=payload.state,
    )
    await session.commit()

    trip_ids = await _replan_medicines(session, payload.state, skus)
    trips = await redistribution.list_transfers(session, ids=trip_ids)
    view = await outbreak.evaluate(session, row, now)
    return DeclaredOutbreakOut(
        outbreak=_active_out(view, await outbreak.warnings(session, view, now)),
        trips_proposed=len(trips),
        pre_positioning_trips=sum(
            1 for t in trips if (t.get("rationale") or {}).get("outbreak")
        ),
    )


class IdspReportIn(BaseModel):
    pdf_base64: str
    filename: str | None = Field(default=None, max_length=200)


class IdspRowOut(BaseModel):
    unique_id: str
    year: int
    week: int
    state: str
    state_code: str | None
    district: str
    disease: str
    cases: int
    deaths: int
    start_date: str | None
    reported_date: str | None
    status: str | None
    row_text: str
    check: dict
    in_network: bool = False
    activated: bool = False


class IdspReportOut(BaseModel):
    year: int | None
    week: int | None
    source: str | None
    model: str
    read_by: str | None
    read_at: datetime | None
    cached: bool
    rows: list[IdspRowOut]
    dropped: int
    agrees: int
    disagrees: int
    unparsed: int
    activated: int
    trips_proposed: int = 0


def _idsp_out(report: IdspReport, *, cached: bool, trips: int = 0) -> IdspReportOut:
    rows = [IdspRowOut(**r) for r in report.rows]
    verdicts = [r.check.get("verdict") for r in rows]
    return IdspReportOut(
        year=report.year, week=report.week, source=report.source, model=report.model,
        read_by=report.read_by, read_at=report.read_at, cached=cached, rows=rows,
        dropped=report.dropped or 0,
        agrees=verdicts.count("agrees"), disagrees=verdicts.count("disagrees"),
        unparsed=verdicts.count("unparsed"),
        activated=sum(1 for r in rows if r.activated), trips_proposed=trips,
    )


async def ingest_idsp_pdf(
    session: AsyncSession,
    pdf: bytes,
    *,
    source: str | None,
    read_by: str,
    allowed,
) -> IdspReportOut:
    """Read one report with the model, cross-check it, keep it, and turn the
    rows both readings agree on into active outbreaks (#41). A report already
    read is returned from the table; the model is never asked twice."""
    digest = hashlib.sha256(pdf).hexdigest()
    cached = await session.scalar(select(IdspReport).where(IdspReport.sha256 == digest))
    if cached is not None:
        return _idsp_out(cached, cached=True)
    try:
        read = await vision.read_idsp_report(pdf)
    except vision.VisionError as exc:
        status = 503 if "live model" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    rows = [{**r, "check": idsp.cross_check(r)} for r in read.rows]
    touched = await outbreak.activate_idsp(
        session, rows, now,
        allowed=allowed,
    )
    report = IdspReport(
        sha256=digest, year=read.year, week=read.week, source=source, model=read.model,
        read_by=read_by, read_at=now, rows=rows, dropped=read.dropped,
    )
    session.add(report)
    await session.flush()
    # Retention: the newest reports only, pruned in the same write.
    keep = select(IdspReport.id).order_by(IdspReport.read_at.desc()).limit(settings.idsp_reports_kept)
    await session.execute(delete(IdspReport).where(IdspReport.id.not_in(keep)))
    await events.record(
        session,
        events.IDSP_READ,
        {"year": read.year, "week": read.week, "rows": len(rows), "model": read.model,
         "activated": [o.id for o in touched]},
    )
    await session.commit()

    trips = 0
    by_state: dict[str, set[str]] = {}
    for o in touched:
        by_state.setdefault(o.state_silo or "", set()).update(
            outbreak.medicines_for(o.disease_category or "")
        )
    for state, skus in by_state.items():
        trips += len(await _replan_medicines(session, state, sorted(skus)))
    return _idsp_out(report, cached=False, trips=trips)


@router.post("/outbreaks/idsp-report", response_model=IdspReportOut, tags=["outbreaks"])
async def read_idsp_report(
    payload: IdspReportIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> IdspReportOut:
    """An officer gives the platform an IDSP weekly report PDF; Gemini reads
    it (fix #42). Public demo accounts read NCDC's latest report instead, so a
    stranger cannot spend the model's daily quota on uploads."""
    if user.role not in ("admin", "state_officer"):
        raise HTTPException(status_code=403, detail="Only state and national officers read IDSP reports")
    if is_public_demo(user):
        raise HTTPException(
            status_code=403,
            detail="Uploading a report is for officer accounts; the public demo reads NCDC's latest report instead",
        )
    try:
        pdf = base64.b64decode(payload.pdf_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="The report is not valid base64") from exc
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="That file is not a PDF")
    if len(pdf) > vision.MAX_REPORT_BYTES:
        raise HTTPException(status_code=413, detail="The report is too large")
    return await ingest_idsp_pdf(
        session, pdf, source=payload.filename, read_by=user.name,
        allowed=lambda st, d: can_plan_state(user, st) and demo_may_write(user, state=st, district=d),
    )


# ======================================================== NCDC intake (#57) ===
# Check-on-use plus a button (Aditya's decision), never a scheduler. Opening
# the outbreak panel checks NCDC when the last check is over a day old, after
# the response is sent; an officer may press "Check NCDC now" every few
# minutes. Only a week newer than the last report read is fetched, and the
# model reads it once (#42). Every check is an event, so the panel can say
# when NCDC was last checked and what it found.

_ncdc_lock = asyncio.Lock()


async def _last_ncdc_check(session: AsyncSession) -> Event | None:
    return await session.scalar(
        select(Event).where(Event.kind == events.IDSP_CHECKED).order_by(Event.created_at.desc()).limit(1)
    )


async def run_ncdc_check(session: AsyncSession, requested_by: str) -> dict:
    """One check of NCDC's listing, and a read of the newest report if it is
    new. Rows from an official report activate wherever the network has
    centres, whoever asked: the report, not the asker, is the authority."""
    async with _ncdc_lock:
        payload: dict = {"requested_by": requested_by}
        try:
            listed = await ncdc.fetch_latest()
        except ncdc.NcdcError as exc:
            listed = None
            payload.update(status="unreachable", detail=str(exc))
        if listed is not None:
            payload.update(
                year=listed.year, week=listed.week, url=listed.url,
                uploaded_on=listed.uploaded_on.isoformat(),
            )
            last = await session.scalar(
                select(IdspReport).order_by(IdspReport.year.desc(), IdspReport.week.desc()).limit(1)
            )
            last_week = (last.year, last.week) if last and last.year and last.week else None
            if not ncdc.is_newer((listed.year, listed.week), last_week):
                payload.update(status="up_to_date")
            else:
                try:
                    pdf = await ncdc.fetch_pdf(listed.url)
                    out = await ingest_idsp_pdf(
                        session, pdf, source=listed.url,
                        read_by=f"NCDC check ({requested_by})", allowed=lambda st, d: True,
                    )
                    payload.update(
                        status="read", rows=len(out.rows), agrees=out.agrees,
                        activated=out.activated, trips=out.trips_proposed,
                    )
                except ncdc.NcdcError as exc:
                    payload.update(status="found_unread", detail=str(exc))
                except HTTPException as exc:
                    payload.update(status="found_unread", detail=str(exc.detail))
        elif "status" not in payload:
            payload.update(status="no_reports")
        await events.record(session, events.IDSP_CHECKED, payload)
        return payload


async def _check_ncdc_in_background(requested_by: str) -> None:
    async with SessionLocal() as session:
        last = await _last_ncdc_check(session)
        if ncdc.check_due(last.created_at if last else None, datetime.now(timezone.utc)):
            await run_ncdc_check(session, requested_by)


class NcdcStatusOut(BaseModel):
    checked_at: datetime | None
    result: dict | None
    checking: bool


@router.get("/outbreaks/ncdc-status", response_model=NcdcStatusOut, tags=["outbreaks"])
async def ncdc_status(
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> NcdcStatusOut:
    """When NCDC was last checked and what it said. If that was more than a
    day ago, a check runs after this response (check-on-use)."""
    last = await _last_ncdc_check(session)
    due = ncdc.check_due(last.created_at if last else None, datetime.now(timezone.utc))
    if due and not _ncdc_lock.locked():
        background.add_task(_check_ncdc_in_background, user.name)
    return NcdcStatusOut(
        checked_at=last.created_at if last else None,
        result=last.payload if last else None,
        checking=due or _ncdc_lock.locked(),
    )


@router.post("/outbreaks/ncdc-check", tags=["outbreaks"])
async def ncdc_check(
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> dict:
    """"Check NCDC now": an officer's button, at most every few minutes."""
    if user.role == "facility_user":
        raise HTTPException(status_code=403, detail="Officers check NCDC for new reports")
    if _ncdc_lock.locked():
        raise HTTPException(status_code=409, detail="A check is already running")
    last = await _last_ncdc_check(session)
    now = datetime.now(timezone.utc)
    if not ncdc.may_check_now(last.created_at if last else None, now):
        wait = math.ceil((ncdc.BUTTON_COOLDOWN - (now - last.created_at)).total_seconds() / 60)
        raise HTTPException(
            status_code=429,
            detail=f"NCDC was checked moments ago; try again in {wait} minute{'' if wait == 1 else 's'}",
        )
    result = await run_ncdc_check(session, user.name)
    return {"checked_at": datetime.now(timezone.utc), "result": result}


@router.get("/outbreaks/idsp-reports/latest", response_model=IdspReportOut | None, tags=["outbreaks"])
async def latest_idsp_report(
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> IdspReportOut | None:
    report = await session.scalar(
        select(IdspReport).order_by(IdspReport.read_at.desc()).limit(1)
    )
    return _idsp_out(report, cached=True) if report is not None else None


@router.post("/outbreaks/{outbreak_id}/end", tags=["outbreaks"])
async def end_outbreak(
    outbreak_id: int,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> dict:
    row = await session.get(OutbreakEvent, outbreak_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such outbreak")
    refusal = outbreak.may_declare(user, row.state_silo or "", row.district or "")
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    if row.ended_at is None:
        row.ended_at = datetime.now(timezone.utc)
        await events.record(
            session,
            events.OUTBREAK_ENDED,
            {
                "outbreak_id": row.id,
                "state": row.state_silo,
                "district": row.district,
                "disease": row.disease_category,
                "ended_by": user.name,
            },
            state_silo=row.state_silo,
        )
        await session.commit()
        await _replan_medicines(
            session, row.state_silo or "", outbreak.medicines_for(row.disease_category or "")
        )
    return {"id": row.id, "ended_at": row.ended_at}


# ==================================================== the ingestion spine ===
# Spec 13. Every phone channel lands here, and this endpoint drives the same
# pipeline with no Twilio account in existence — which is what keeps the
# COMMS_MODE=simulator path a tested path rather than a claim.


class SimulateIn(BaseModel):
    channel: str = "sms"
    sender: str
    text: str | None = None
    external_id: str | None = None


class SimulatedReadingOut(BaseModel):
    sku_code: str
    qty: float
    days_of_stock: float | None
    status: str | None
    # The loop, closed where the sender can see it (fix list #24): the row
    # written, and what the district map showed for this medicine before it.
    reading_id: int | None = None
    qty_before: float | None = None
    days_before: float | None = None
    status_before: str | None = None


class SimulateOut(BaseModel):
    accepted: bool
    stage: str
    reply: str
    facility_id: str | None
    masked_sender: str | None
    duplicate: bool
    readings: list[SimulatedReadingOut]
    actions: list[str]
    # The event the district map and the live feed poll for; None when
    # nothing was committed.
    event_id: int | None = None
    # Rows written other than stock readings, in words: "check-in #412".
    written: list[str] = []


class HandsetOut(BaseModel):
    role: str
    number: str
    masked: str
    # Whether the registry actually answers this number. It is derived from
    # the facility id, but the registry stores a *salted hash* of it — so a
    # registry seeded under a different PHONE_HASH_SALT does not recognise
    # numbers this endpoint would happily hand out. Reporting it here means
    # the simulator cannot offer a handset that will fail, and a salt mismatch
    # shows up immediately instead of as "not registered to a facility".
    registered: bool


@router.get(
    "/facilities/{facility_id}/handsets",
    response_model=list[HandsetOut],
    tags=["ingest"],
)
async def facility_handsets(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[HandsetOut]:
    """The demo handsets registered to a facility, so the field simulator has
    numbers to type.

    Demo mode only, and these are not phone numbers in any real sense: they
    are derived from the facility id by `ingest.demo_number` and never stored
    — the registry holds only a salted hash of each. There is nothing here to
    leak, which is the whole point of the design that produced them.
    """
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    # A handset is a way to write for its centre, so it is offered only to
    # whoever may state that centre's facts: its own staff, or a public demo
    # account inside the sandbox.
    _require_own_handset(user, facility)
    out: list[HandsetOut] = []
    for role in ("reporter", "supervisor"):
        number = ingest.demo_number(facility_id, role)
        contact = await ingest.identify(session, number)
        out.append(
            HandsetOut(
                role=role,
                number=number,
                masked=ingest.mask_phone(number),
                registered=contact is not None and contact.facility_id == facility_id,
            )
        )
    return out


@router.post("/ingest/simulate", response_model=SimulateOut, tags=["ingest"])
async def ingest_simulate(
    payload: SimulateIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> SimulateOut:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    # The sender decides which centre the message writes for, so the sender
    # must be a handset registered to a centre this account may state facts
    # for: a pharmacist, only their own centre's (fix list #24); a public demo
    # account, a sandbox centre's. An unregistered number belongs to no
    # centre and writes nothing: the spine refuses it at identify.
    contact = await ingest.identify(session, payload.sender)
    if contact is not None:
        owner = await session.get(Facility, contact.facility_id)
        if owner is not None:
            _require_own_handset(user, owner)
    submission = ingest.RawSubmission(
        channel=payload.channel,
        sender_ref=payload.sender,
        # A simulated send still needs an id, or the dedupe stage has nothing
        # to work with; a real channel supplies the provider's message id.
        external_id=payload.external_id or f"sim:{uuid4()}",
        text=payload.text,
    )
    outcome = await ingest.process(session, submission)
    return SimulateOut(
        accepted=outcome.accepted,
        stage=outcome.stage,
        reply=outcome.reply,
        facility_id=outcome.facility_id,
        masked_sender=outcome.masked_sender,
        duplicate=outcome.duplicate,
        readings=[
            SimulatedReadingOut(
                sku_code=r.sku_code,
                qty=r.qty,
                days_of_stock=r.days_of_stock,
                status=r.status,
                reading_id=r.reading_id,
                qty_before=r.qty_before,
                days_before=r.days_before,
                status_before=r.status_before,
            )
            for r in outcome.readings
        ],
        actions=outcome.actions,
        event_id=outcome.event_id,
        written=outcome.written,
    )


# ==========================================================================
# Inbound webhooks — spec 13, 14, and rule 1.9.
#
# These are the only unauthenticated write endpoints in the system, and they
# are the only ones reachable from the public internet by a stranger. So the
# rule is absolute and is enforced before anything else happens: **every
# inbound webhook validates its provider signature before processing. No
# exceptions.**
#
# That includes simulator mode. With no auth token configured there is no
# signature that can validate, so these routes refuse everything — which is
# correct rather than inconvenient. A door that opens for anyone "because we
# are only testing" is a door. The field simulator drives
# /api/ingest/simulate instead, behind a session like every other route.
#
# They live on `public_router` because Twilio has no session, and
# test_api_boundary.py pins the exact list of routes that answer without one —
# so adding these was a deliberate edit to that list, reviewed as a one-line
# diff.
# ==========================================================================


class WebhookReply(BaseModel):
    """What the adapter did. Twilio ignores the body when the status is 204;
    this shape exists so the checks can assert on it."""

    accepted: bool
    stage: str
    reply: str | None = None
    facility_id: str | None = None
    duplicate: bool = False


async def _verified_form(request: Request) -> dict[str, str]:
    """The POST parameters, once the request has proved it came from Twilio.

    Raises 403 otherwise, and says nothing about why: a webhook that explains
    which half of the signature was wrong is a webhook that helps somebody
    guess the other half.
    """
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    url = comms.webhook_url(
        request.url.scheme,
        request.headers.get("host", request.url.netloc),
        request.url.path,
        request.url.query,
    )
    if not comms.validate_signature(
        url, params, request.headers.get("X-Twilio-Signature")
    ):
        raise HTTPException(status_code=403, detail="Signature check failed")
    return params


async def _ingest_and_reply(
    session: AsyncSession, channel: str, params: dict[str, str]
) -> WebhookReply:
    """One inbound message through the spine, then the acknowledgement.

    The reply is sent after the commit and its failure is swallowed: the
    reading is already recorded, and losing an acknowledgement must never
    undo it.
    """
    submission = ingest.RawSubmission(
        channel=channel,
        sender_ref=params.get("From", ""),
        external_id=params.get("MessageSid") or params.get("SmsSid") or "",
        text=params.get("Body"),
        # Twilio hosts the media and gives a URL; the bytes are fetched by the
        # adapter, read once, and never stored. Only this reference is kept.
        media_ref=params.get("MediaUrl0"),
    )
    outcome = await ingest.process(session, submission)

    try:
        await comms.send(
            session,
            channel=channel,
            to_ref=outcome.masked_sender or "unknown",
            body=outcome.reply,
        )
        await session.commit()
    except comms.CommsError as exc:
        await session.rollback()
        log.warning("inbound %s acknowledged but reply not sent: %s", channel, exc)

    return WebhookReply(
        accepted=outcome.accepted,
        stage=outcome.stage,
        reply=outcome.reply,
        facility_id=outcome.facility_id,
        duplicate=outcome.duplicate,
    )


@public_router.post("/webhooks/sms", response_model=WebhookReply, tags=["webhooks"])
async def twilio_sms(
    request: Request, session: AsyncSession = Depends(get_session)
) -> WebhookReply:
    """An SMS from a registered handset. Spec 13."""
    params = await _verified_form(request)
    return await _ingest_and_reply(session, "sms", params)


@public_router.post("/webhooks/whatsapp", response_model=WebhookReply, tags=["webhooks"])
async def twilio_whatsapp(
    request: Request, session: AsyncSession = Depends(get_session)
) -> WebhookReply:
    """A WhatsApp message, which may carry a ward photo. Spec 14.

    Twilio prefixes WhatsApp numbers with `whatsapp:`; the spine normalises
    that away when it hashes the sender, so one handset is one contact however
    it reached us.
    """
    params = await _verified_form(request)
    return await _ingest_and_reply(session, "whatsapp", params)


@public_router.post("/webhooks/voice/status", tags=["webhooks"])
async def twilio_voice_status(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """A call's final status. Spec 15.

    Records a reference and an outcome and nothing else. No audio, no
    transcript, no number: Twilio keeps the call record on its own side, and
    duplicating it here would make this database a log of who rang whom.
    """
    params = await _verified_form(request)
    call_ref = params.get("CallSid", "")
    outcome = params.get("CallStatus", "")
    if not call_ref or outcome not in CALL_OUTCOMES:
        raise HTTPException(status_code=422, detail="Unusable call status")

    contact = await ingest.identify(session, params.get("From", ""))
    await comms.record_call(
        session,
        call_ref=call_ref,
        outcome=outcome,
        facility_id=contact.facility_id if contact else None,
        direction=params.get("Direction", "inbound").startswith("outbound")
        and "outbound"
        or "inbound",
    )
    await session.commit()
    return {"recorded": True, "call_ref": call_ref, "outcome": outcome}


@router.get("/calls", tags=["webhooks"])
async def list_calls(
    limit: int = Query(default=50, le=200),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> list[dict]:
    """The call log, for the demo panel, inside the caller's scope (fix #77):
    a call is a row about the centre it was placed to. Capped by the table."""
    narrowed = _narrow_to_rows_scope(user, None, None)
    if narrowed is None and user.role != "facility_user":
        return []
    stmt = select(CallLog).join(Facility, Facility.id == CallLog.facility_id)
    if user.role == "facility_user":
        stmt = stmt.where(CallLog.facility_id == user.facility_id)
    else:
        stmt = stmt.where(Facility.state_silo == narrowed[0])
        if narrowed[1]:
            stmt = stmt.where(Facility.district == narrowed[1])
    rows = (
        await session.execute(stmt.order_by(CallLog.created_at.desc()).limit(limit))
    ).scalars().all()
    return [
        {
            "call_ref": r.call_ref,
            "facility_id": r.facility_id,
            "direction": r.direction,
            "outcome": r.outcome,
            "created_at": r.created_at,
        }
        for r in rows
    ]

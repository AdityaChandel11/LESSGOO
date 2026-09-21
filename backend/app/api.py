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

import base64
import binascii
from uuid import uuid4
from dataclasses import asdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import (
    ingest,
    aggregates,
    attendance,
    beds,
    events,
    movements,
    redistribution,
    services,
    trust,
    vision,
    workspace,
)
from .auth import (
    Principal,
    can_decide_transfer,
    can_plan_state,
    can_submit_reading,
    can_view_facility,
    current_user,
)
from .config import settings
from .db import get_session, ping
from .models import (
    LOC_METHODS,
    Facility,
    FederationRound,
    MedicineMovement,
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
) -> list[PinOut]:
    corners = (south, west, north, east)
    if any(c is not None for c in corners) and not all(c is not None for c in corners):
        raise HTTPException(status_code=422, detail="bbox needs south, west, north and east")
    bbox = corners if all(c is not None for c in corners) else None
    if bbox is None and state is None:
        raise HTTPException(status_code=422, detail="Provide a bbox or a state")
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
        {"state": payload.state, "sku": payload.sku, **totals},
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


@router.get("/transfers", response_model=list[TransferOut], tags=["transfers"])
async def get_transfers(
    state: str | None = Query(default=None),
    status: str | None = Query(default=None, description="comma-separated"),
    limit: int = Query(default=300, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[TransferOut]:
    statuses = [s.strip() for s in status.split(",")] if status else None
    rows = await redistribution.list_transfers(
        session, state=state, statuses=statuses, limit=limit
    )
    return [TransferOut.model_validate(r) for r in rows]


async def _decide(
    transfer_id: int, decision: str, session: AsyncSession, user: Principal
) -> TransferOut:
    existing = await redistribution.list_transfers(session, ids=[transfer_id])
    if not existing:
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
    if not can_decide_transfer(
        user,
        from_state=src.state_silo,
        from_district=src.district,
        to_state=dst.state_silo,
        to_district=dst.district,
    ):
        raise HTTPException(
            status_code=403,
            detail="This transfer is outside the area you are responsible for",
        )

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
@router.get("/facilities", response_model=list[FacilityOut], tags=["facilities"])
async def list_facilities(
    session: AsyncSession = Depends(get_session),
    district: str | None = Query(default=None),
    state_silo: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[FacilityOut]:
    snaps = await services.get_snapshots(session)
    if district:
        snaps = [s for s in snaps if s.district == district]
    if state_silo:
        snaps = [s for s in snaps if s.state_silo == state_silo]
    if status:
        snaps = [s for s in snaps if s.status == status]
    # Worst first: an officer opening this should see the fires at the top.
    snaps.sort(key=lambda s: (-services.STATUS_ORDER.index(s.status), s.name))
    return [_to_out(s) for s in snaps]


@router.get(
    "/facilities/{facility_id}",
    response_model=FacilityDetailOut,
    tags=["facilities"],
)
async def get_facility(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
) -> FacilityDetailOut:
    snaps = await services.get_snapshots(session, facility_ids=[facility_id])
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


class WorkspaceOut(BaseModel):
    facility: FacilityOut
    skus: list[WorkspaceSkuOut]
    open_requests: int
    max_open_requests: int
    # Both languages of the computed line, always present and needing no key.
    # The screen renders this on load; the model is an overlay on top of it.
    briefing: dict[str, str]


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
        raise HTTPException(
            status_code=403,
            detail="You can only open the workspace for your own facility",
        )
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
    snaps = await services.get_snapshots(session, facility_ids=[facility.id])
    if not snaps:
        raise HTTPException(status_code=404, detail="Facility not found")
    snap = snaps[0]

    receipts = await workspace.last_receipts(session, facility.id)
    open_here, _ = await workspace.open_request_counts(session, facility.id)
    units = {
        row.code: row.unit
        for row in (await session.execute(select(Sku))).scalars().all()
    }
    now = datetime.now(timezone.utc)

    rows: list[WorkspaceSkuOut] = []
    for s in snap.skus:
        receipt = receipts.get(s.sku_code)
        prov = workspace.provenance(s.last_source, s.last_reported_at, receipt, now)
        rows.append(
            WorkspaceSkuOut(
                **vars(s),
                unit=units.get(s.sku_code, "unit"),
                last_receipt=LastReceiptOut(**vars(receipt)) if receipt else None,
                provenance=ProvenanceOut(**vars(prov)),
                stockout_on=workspace.stockout_date(s.days_of_stock, s.last_reported_at),
            )
        )

    return WorkspaceOut(
        facility=_to_out(snap),
        skus=rows,
        open_requests=open_here,
        max_open_requests=settings.max_open_requests_per_facility,
        briefing=workspace.rules_briefing(_briefing_rows(snap.skus, units)),
    )


class BriefingOut(BaseModel):
    """One line of "what to do today".

    `ai` is the only thing the screen may use to decide whether to put a model's
    name on it. When the model is unavailable, out of quota or switched off,
    this comes back with the deterministic line, `source="rules"` and
    `ai=False` — the app never presents computed text as a model's work.
    """

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


def _briefing_rows(skus: list[services.SkuStock], units: dict[str, str]) -> list[workspace.BriefingRow]:
    return [
        workspace.BriefingRow(
            sku_name=s.sku_name,
            unit=units.get(s.sku_code, "unit"),
            qty=s.qty_on_hand,
            days_of_stock=s.days_of_stock,
            status=s.status,
        )
        for s in skus
    ]


@router.post(
    "/facilities/{facility_id}/briefing",
    response_model=BriefingOut,
    tags=["workspace"],
)
async def facility_briefing(
    facility_id: str,
    lang: str = Query(default="en", pattern="^(en|hi)$"),
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> BriefingOut:
    """Ask the model for today's line. POST, and only ever from a click.

    Never called on page load: the free tier allows 20 generate requests a day
    per model, and a screen that spent one on every render would be out of
    quota before the first demo finished. The deterministic line already
    travels with the workspace payload, so this endpoint is an overlay on
    something that is already on screen, not the thing that fills it.
    """
    facility = await _facility_in_scope(session, facility_id, user)
    snaps = await services.get_snapshots(session, facility_ids=[facility.id])
    if not snaps:
        raise HTTPException(status_code=404, detail="Facility not found")

    units = {
        row.code: row.unit for row in (await session.execute(select(Sku))).scalars().all()
    }
    rows = _briefing_rows(snaps[0].skus, units)
    fallback = workspace.rules_briefing(rows)
    inputs_hash = workspace.briefing_hash(rows)

    def rules_answer(note: str | None) -> BriefingOut:
        return BriefingOut(
            body=fallback[lang], lang=lang, source="rules", ai=False,
            model=None, generated_at=None, cached=False, note=note,
        )

    if not vision.briefing_available():
        return rules_answer(None)

    cached = await workspace.cached_briefing(session, facility.id, lang, inputs_hash)
    if cached is not None:
        return BriefingOut(
            body=cached.body, lang=lang, source="gemini", ai=True,
            model=cached.model, generated_at=cached.generated_at, cached=True,
        )

    try:
        written = await vision.write_briefing(
            facility_name=facility.name,
            rows=workspace.briefing_rows_from_skus(rows),
        )
    except vision.VisionError as exc:
        # One attempt, then the computed line. No retry loop: the caller is a
        # person clicking a button, and a spent quota does not recover in the
        # time it takes to try again.
        return rules_answer("Showing the computed line — {0}.".format(exc))

    await workspace.store_briefing(
        session, facility.id,
        {"en": written.en, "hi": written.hi},
        inputs_hash=inputs_hash, model=written.model,
    )
    await session.commit()

    return BriefingOut(
        body=written.en if lang == "en" else written.hi,
        lang=lang, source="gemini", ai=True, model=written.model,
        generated_at=datetime.now(timezone.utc), cached=False,
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
    nothing else: no stock moves until an officer approves it (spec 12.3 —
    nothing auto-executes)."""
    facility = await _facility_in_scope(session, facility_id, user)
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
    estimate = workspace.delivery_estimate(km=km, raised_at=now)
    eta_hours = round(km / rules.avg_speed_kmh + rules.handling_hours, 2)
    approver_role = (
        "block_mo"
        if donor_facility.district == facility.district
        and donor_facility.state_silo == facility.state_silo
        else "state_officer"
    )

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
    if not can_submit_reading(
        user,
        facility_id=facility.id,
        facility_state=facility.state_silo,
        facility_district=facility.district,
    ):
        raise HTTPException(
            status_code=403, detail="You can only report stock for facilities you are responsible for"
        )
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


@router.get("/events", response_model=EventsOut, tags=["realtime"])
async def poll_events(
    after: int | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_session),
) -> EventsOut:
    return EventsOut.model_validate(await events.since(session, after))


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

    rows = await movements.list_movements(
        session, state=state, district=district, facility_id=facility,
        sku=sku, view=view, limit=limit, offset=offset,
    )
    totals = await movements.summary(
        session, state=state, district=district, facility_id=facility, sku=sku
    )
    return MovementsOut(
        view=view,
        counts=totals["counts"],
        short_units=totals["short_units"],
        movements=[MovementOut(**asdict(r)) for r in rows],
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
    # carries the same permission as submitting a stock reading.
    if not can_submit_reading(
        user,
        facility_id=facility.id,
        facility_state=facility.state_silo,
        facility_district=facility.district,
    ):
        raise HTTPException(
            status_code=403,
            detail="You can only confirm deliveries for facilities you are responsible for",
        )
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
    facility = await _facility_for_report(session, facility_id, user)
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
        reasons=payload.get("reasons", []),
    )


async def _facility_for_report(
    session: AsyncSession, facility_id: str, user: Principal
) -> Facility:
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Unknown facility")
    if not can_submit_reading(
        user,
        facility_id=facility.id,
        facility_state=facility.state_silo,
        facility_district=facility.district,
    ):
        raise HTTPException(
            status_code=403,
            detail="You can only report for facilities you are responsible for",
        )
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
    if await session.get(Facility, facility_id) is None:
        raise HTTPException(status_code=404, detail="Unknown facility")
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


@router.get(
    "/facilities/{facility_id}/attendance", response_model=AttendanceOut, tags=["attendance"]
)
async def facility_attendance(
    facility_id: str,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> AttendanceOut:
    if await session.get(Facility, facility_id) is None:
        raise HTTPException(status_code=404, detail="Unknown facility")
    return AttendanceOut(**asdict(await attendance.summarise(session, facility_id)))


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


class SimulateOut(BaseModel):
    accepted: bool
    stage: str
    reply: str
    facility_id: str | None
    masked_sender: str | None
    duplicate: bool
    readings: list[SimulatedReadingOut]
    actions: list[str]


@router.post("/ingest/simulate", response_model=SimulateOut, tags=["ingest"])
async def ingest_simulate(
    payload: SimulateIn,
    session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> SimulateOut:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
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
                sku_code=r.sku_code, qty=r.qty, days_of_stock=r.days_of_stock, status=r.status
            )
            for r in outcome.readings
        ],
        actions=outcome.actions,
    )

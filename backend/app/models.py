"""ORM models — spec Section 9.

Deviation from spec, recorded deliberately: the `geom GEOGRAPHY(POINT,4326)`
column and its GIST index are omitted. PostGIS is not installed in this
environment, and its only use in the spec is radius filtering for the outbreak
layer (12.5). At 80-120 facilities a haversine expression in plain SQL is
equivalent and removes a native-extension dependency — the same reasoning the
spec applies to OR-Tools in Section 7. Revisit only if facility count grows by
orders of magnitude.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

FACILITY_TYPES = ("PHC", "CHC", "SUBCENTRE")
USER_ROLES = ("admin", "state_officer", "block_mo", "facility_user")
READING_SOURCES = (
    "form", "voice", "photo", "sms", "ivr", "whatsapp", "seed", "transfer",
)
TRANSFER_STATUSES = ("proposed", "approved", "rejected", "completed")
# Settled states of a batch. "Overdue" is derived from expected_by when read,
# never stored — see MedicineMovement.
MOVEMENT_STATUSES = ("in_transit", "received", "short", "over", "cancelled")
DISPATCH_SOURCES = ("warehouse", "transfer", "seed")
RECEIPT_CHANNELS = ("form", "sms", "ivr", "whatsapp", "photo")
VERIFICATION_STATES = ("verified", "unverified", "rejected")
TRUST_BANDS = ("good", "watch", "audit")
# What produced a location, recorded on every geofenced row so the UI can say
# which check actually ran (spec 26.1).
LOC_METHODS = ("gps", "cell_id", "none", "simulated")


class Facility(Base):
    __tablename__ = "facilities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    state_silo: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    district: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    lat: Mapped[float] = mapped_column(nullable=False)
    lng: Mapped[float] = mapped_column(nullable=False)
    beds_total: Mapped[int] = mapped_column(Integer, default=0)

    stock_readings: Mapped[list["StockReading"]] = relationship(
        back_populates="facility", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"type IN {FACILITY_TYPES}", name="ck_facilities_type"),
    )


class User(Base):
    """A person who signs in. Scope narrows what they may change, never what
    they may see — the national picture is visible to every signed-in user.

    `state_silo`, `district` and `facility_id` are deliberately not foreign
    keys. Facility data is reloaded with TRUNCATE ... CASCADE, which would
    otherwise delete every account along with it; they are validated when an
    account is created instead.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    state_silo: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    facility_id: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"role IN {USER_ROLES}", name="ck_users_role"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )


class LoginFailure(Base):
    """One failed sign-in. Kept in the database, not in process memory, so the
    limit holds however many server instances are running."""

    __tablename__ = "login_failures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_login_failures_email_at", "email", "at"),
        Index("ix_login_failures_ip_at", "ip", "at"),
    )


class Event(Base):
    """Durable change log that browsers poll.

    Every change worth showing live (a stock report, a status change, a
    transfer decision) is written here, and each browser asks for events after
    the last id it saw. Because it lives in the database, any server instance
    can answer, and nothing depends on a long-lived connection surviving a
    hosting proxy.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state_silo: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class Sku(Base):
    __tablename__ = "skus"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Fuzzy-match targets for speech and OCR variants (spec Section 10.2).
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text), default=list)
    unit: Mapped[str] = mapped_column(Text, default="unit")
    is_controlled: Mapped[bool] = mapped_column(Boolean, default=False)
    cold_chain: Mapped[bool] = mapped_column(Boolean, default=False)


class StockReading(Base):
    __tablename__ = "stock_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"), index=True)
    qty_on_hand: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    footfall_same_day: Mapped[int | None] = mapped_column(Integer)

    # Provenance (v3). reporter_ref is a salted hash, never a raw phone number.
    reporter_ref: Mapped[str | None] = mapped_column(Text)
    channel_msg_id: Mapped[str | None] = mapped_column(Text, unique=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    # Indexed: without it, deleting readings is quadratic, because Postgres
    # scans this self-referencing column once per deleted row.
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("stock_readings.id"), index=True
    )

    facility: Mapped[Facility] = relationship(back_populates="stock_readings")

    __table_args__ = (
        CheckConstraint(f"source IN {READING_SOURCES}", name="ck_stock_readings_source"),
        Index("ix_readings_facility_sku_time", "facility_id", "sku_code", "reported_at"),
    )


class FacilitySkuState(Base):
    """Current stock position per facility per SKU.

    Denormalised on purpose. The national map needs per-state and per-district
    rollups across thousands of facilities on every pan and zoom; recomputing
    burn rates from the full reading history for that is not viable. Readings
    remain the append-only source of truth, and every commit updates this row.
    """

    __tablename__ = "facility_sku_state"

    facility_id: Mapped[str] = mapped_column(
        ForeignKey("facilities.id"), primary_key=True
    )
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"), primary_key=True)
    qty_on_hand: Mapped[float] = mapped_column(nullable=False, default=0.0)
    daily_burn_rate: Mapped[float] = mapped_column(nullable=False, default=0.0)
    days_of_stock: Mapped[float | None] = mapped_column()
    # Denormalised traffic light so the map can filter and group without
    # re-deriving thresholds in SQL.
    status: Mapped[str] = mapped_column(Text, nullable=False, default="healthy")
    last_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_source: Mapped[str | None] = mapped_column(Text)
    last_confidence: Mapped[float | None] = mapped_column()

    __table_args__ = (
        Index("ix_fss_status", "status"),
        Index("ix_fss_sku_status", "sku_code", "status"),
    )


class MedicineMovement(Base):
    """One batch moving from a source to a facility — spec 26.3.

    Two-sided on purpose. The dispatch half is written where the batch leaves
    (state warehouse feed, or an approved transfer of ours); the receipt half is
    confirmed separately by the facility that receives it. Neither side can
    quietly settle a discrepancy alone, so a short delivery or a batch that
    never arrives surfaces by itself rather than waiting for an audit.

    `status` holds only what has actually been settled. "Overdue" is not stored:
    it is `in_transit` past `expected_by`, derived when read, so the truth
    changes with the clock rather than with a nightly sweep that can fail.
    """

    __tablename__ = "medicine_movements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(Text, nullable=False)
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"), index=True)
    # Warehouse code (e.g. 'WH-MH-NASHIK') or a facility id for our transfers.
    from_ref: Mapped[str] = mapped_column(Text, nullable=False)
    to_facility: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    # Denormalised from the destination so state officers can filter movements
    # without joining the whole facility table on every page of the ledger.
    state_silo: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    qty_dispatched: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    dispatch_source: Mapped[str] = mapped_column(Text, nullable=False)
    transfer_id: Mapped[int | None] = mapped_column(ForeignKey("transfers.id"))
    expected_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    qty_received: Mapped[Decimal | None] = mapped_column(Numeric)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_via: Mapped[str | None] = mapped_column(Text)
    received_by_ref: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="in_transit")

    __table_args__ = (
        CheckConstraint(
            f"status IN {MOVEMENT_STATUSES}", name="ck_movements_status"
        ),
        CheckConstraint(
            f"dispatch_source IN {DISPATCH_SOURCES}", name="ck_movements_dispatch_source"
        ),
        CheckConstraint("qty_dispatched > 0", name="ck_movements_qty_positive"),
        # One batch of one medicine arrives at one facility once. Without this a
        # retried SMS confirmation could open a second row for the same delivery.
        Index(
            "uq_movement_batch", "batch_id", "to_facility", "sku_code", unique=True
        ),
        # The ledger is read as "what still needs attention", not as history.
        Index("ix_movements_open", "status", "expected_by"),
        Index("ix_movements_facility_time", "to_facility", "dispatched_at"),
    )


class VerificationCode(Base):
    """The day's rotating code for a facility — spec 26.2.

    Issued server-side each morning and delivered by SMS/IVR; it has to appear
    in the ward photo. Unpredictable and single-day, so yesterday's photo
    cannot be resubmitted as today's.
    """

    __tablename__ = "verification_codes"

    facility_id: Mapped[str] = mapped_column(
        ForeignKey("facilities.id"), primary_key=True
    )
    for_date: Mapped[date] = mapped_column(Date, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BedReport(Base):
    """Bed occupancy as a verified observation rather than a typed number.

    Every check that ran is recorded alongside its result, including which
    location method produced the geofence — a simulated signal must never be
    displayed as a verified one (spec 26.1).
    """

    __tablename__ = "bed_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    ward: Mapped[str] = mapped_column(Text, default="general")
    beds_total: Mapped[int | None] = mapped_column(Integer)
    beds_occupied: Mapped[int | None] = mapped_column(Integer)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)

    code_expected: Mapped[str | None] = mapped_column(Text)
    code_read: Mapped[str | None] = mapped_column(Text)
    code_ok: Mapped[bool | None] = mapped_column(Boolean)

    loc_method: Mapped[str | None] = mapped_column(Text)
    loc_lat: Mapped[float | None] = mapped_column()
    loc_lng: Mapped[float | None] = mapped_column()
    loc_accuracy_m: Mapped[float | None] = mapped_column()
    geofence_km: Mapped[float | None] = mapped_column()
    geofence_ok: Mapped[bool | None] = mapped_column(Boolean)

    register_admissions: Mapped[int | None] = mapped_column(Integer)
    model_confidence: Mapped[float | None] = mapped_column()
    media_ref: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    verification: Mapped[str] = mapped_column(Text, nullable=False, default="unverified")

    __table_args__ = (
        CheckConstraint(
            f"verification IN {VERIFICATION_STATES}", name="ck_bed_reports_verification"
        ),
    )


class BedStatus(Base):
    __tablename__ = "bed_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    beds_occupied: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StaffCheckin(Base):
    __tablename__ = "staff_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    # Pseudonymous. Never surfaced as an individual performance metric (rule 8).
    staff_ref: Mapped[str | None] = mapped_column(Text)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shift: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    # Serving cell tower, only when the channel actually supplies one. An
    # inbound Twilio call does not (spec 26.1) — loc_method records the truth.
    cell_id: Mapped[str | None] = mapped_column(Text)
    loc_method: Mapped[str | None] = mapped_column(Text)
    geofence_km: Mapped[float | None] = mapped_column()
    geofence_ok: Mapped[bool | None] = mapped_column(Boolean)
    footfall_same_period: Mapped[int | None] = mapped_column(Integer)


class FacilityTrust(Base):
    """Rolling per-facility data-confidence score — spec 12.6.

    `components` keeps every contributing rule and its sentence, because a
    score an officer cannot interrogate is a score they are right to ignore.
    """

    __tablename__ = "facility_trust"

    facility_id: Mapped[str] = mapped_column(
        ForeignKey("facilities.id"), primary_key=True
    )
    score: Mapped[float] = mapped_column(nullable=False)
    band: Mapped[str] = mapped_column(Text, nullable=False)
    components: Mapped[list] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"band IN {TRUST_BANDS}", name="ck_facility_trust_band"),
        Index("ix_facility_trust_score", "score"),
    )


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_facility: Mapped[str] = mapped_column(ForeignKey("facilities.id"))
    to_facility: Mapped[str] = mapped_column(ForeignKey("facilities.id"))
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"))
    qty: Mapped[Decimal | None] = mapped_column(Numeric)
    route_km: Mapped[Decimal | None] = mapped_column(Numeric)
    eta_hours: Mapped[Decimal | None] = mapped_column(Numeric)
    route_polyline: Mapped[str | None] = mapped_column(Text)
    # 'google_routes' | 'haversine' — the UI must never imply a real road route
    # when it is showing a straight-line estimate.
    route_source: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="proposed", index=True)
    triggered_by: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"status IN {TRANSFER_STATUSES}", name="ck_transfers_status"),
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("transfers.id"), index=True)
    actor_ref: Mapped[str | None] = mapped_column(Text)
    actor_role: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text, default="web")
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TrustFlag(Base):
    __tablename__ = "trust_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    sku_code: Mapped[str | None] = mapped_column(ForeignKey("skus.code"))
    z_score: Mapped[Decimal | None] = mapped_column(Numeric)
    rule: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    flagged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class OutbreakEvent(Base):
    __tablename__ = "outbreak_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district: Mapped[str | None] = mapped_column(Text)
    disease_category: Mapped[str | None] = mapped_column(Text)
    radius_km: Mapped[Decimal | None] = mapped_column(Numeric)
    severity: Mapped[Decimal | None] = mapped_column(Numeric)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RouteMatrixCache(Base):
    __tablename__ = "route_matrix_cache"

    origin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dest_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric)
    duration_min: Mapped[Decimal | None] = mapped_column(Numeric)
    polyline: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    to_ref: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    provider_sid: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ImpactLedger(Base):
    __tablename__ = "impact_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario: Mapped[str | None] = mapped_column(Text)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    stockout_days: Mapped[int | None] = mapped_column(Integer)
    facilities_affected: Mapped[int | None] = mapped_column(Integer)
    transfers_executed: Mapped[int | None] = mapped_column(Integer)
    total_km: Mapped[Decimal | None] = mapped_column(Numeric)
    params: Mapped[dict | None] = mapped_column(JSONB)


class FederationRound(Base):
    __tablename__ = "federation_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_no: Mapped[int] = mapped_column(Integer)
    # Which run this round belonged to, so a second run adds history rather
    # than overwriting the rounds a published claim was computed from.
    run_id: Mapped[str] = mapped_column(Text, index=True)
    strategy: Mapped[str | None] = mapped_column(Text)
    global_val_mae: Mapped[float | None] = mapped_column(Float)
    # What the rule this model replaces scored on the very same windows.
    baseline_mae: Mapped[float | None] = mapped_column(Float)
    # One entry per silo: windows held, live trust, and the trust-weighted
    # count it actually contributed (spec 27).
    per_silo: Mapped[dict | None] = mapped_column(JSONB)
    silos_reporting: Mapped[int | None] = mapped_column(Integer)
    # The inspector's evidence (spec 12.2): what left each silo, and what did not.
    bytes_transmitted: Mapped[int | None] = mapped_column(BigInteger)
    tensor_shapes: Mapped[dict | None] = mapped_column(JSONB)
    weights_sha256: Mapped[str | None] = mapped_column(Text)
    raw_rows_transmitted: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        # A round belongs to exactly one run, so a re-run appends history
        # instead of silently rewriting it.
        Index("uq_federation_round", "run_id", "round_no", unique=True),
    )


class Forecast(Base):
    """Federated model output, per facility and medicine — spec 27 (B5).

    The trained model does not run inside the API. A Cloud Run job publishes
    its predictions here and the web service reads rows, so the image serving
    the dashboard never carries torch and a bad training run can never take
    the site down with it.

    Consumed only when FORECAST_MODE=federated and the row is still fresh;
    otherwise days-of-stock falls back to the burn rate, which needs nothing
    but the readings themselves.
    """

    __tablename__ = "forecasts"

    facility_id: Mapped[str] = mapped_column(
        ForeignKey("facilities.id"), primary_key=True
    )
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"), primary_key=True)
    # Predicted average daily consumption over the coming week.
    predicted_daily_use: Mapped[float] = mapped_column(nullable=False)
    # What the model said relative to the last 28 days: 1.0 is "next week looks
    # like the last four". Kept so a forecast can be read against the rule it
    # replaced without recomputing anything.
    ratio: Mapped[float] = mapped_column(nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

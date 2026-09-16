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
# A phone may report for its facility, or — for a supervisor's number —
# also decide transfers. Never both by accident.
CONTACT_ROLES = ("reporter", "supervisor")


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
    """A declared outbreak — spec 12.5.

    Not a new subsystem: it raises expected demand for the commodities that
    disease actually consumes, inside a radius, for a fixed window. Everything
    downstream — days of cover, the map's colours, the redistribution solver —
    reacts on its own, and every transfer it causes still passes the same
    human-approval gate as any other.
    """

    __tablename__ = "outbreak_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district: Mapped[str | None] = mapped_column(Text)
    state_silo: Mapped[str | None] = mapped_column(Text, index=True)
    disease_category: Mapped[str | None] = mapped_column(Text)
    radius_km: Mapped[float | None] = mapped_column()
    severity: Mapped[float | None] = mapped_column()
    # The centre of the affected area, so the map can draw exactly what was
    # declared rather than a district-shaped guess.
    lat: Mapped[float | None] = mapped_column()
    lng: Mapped[float | None] = mapped_column()
    facilities_affected: Mapped[int | None] = mapped_column(Integer)
    declared_by: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # An outbreak is a period, not a permanent state of the world.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutbreakDemand(Base):
    """Raised demand for one facility and one medicine, while an outbreak runs.

    Kept separate from `forecasts` on purpose. A forecast is what the model
    believes will happen; this is a clinical judgement that a district officer
    declared and can withdraw. Merging them would make it impossible to say
    afterwards which number came from where.
    """

    __tablename__ = "outbreak_demand"

    outbreak_id: Mapped[int] = mapped_column(
        ForeignKey("outbreak_events.id", ondelete="CASCADE"), primary_key=True
    )
    facility_id: Mapped[str] = mapped_column(
        ForeignKey("facilities.id"), primary_key=True
    )
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"), primary_key=True)
    # What normal consumption is multiplied by while this runs.
    multiplier: Mapped[float] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_outbreak_demand_facility", "facility_id", "expires_at"),
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


class FacilityContact(Base):
    """Who may report for a facility over a phone channel — spec 13, step 2.

    The number itself is never stored. An inbound message is normalised and
    hashed with the server's salt, and the hash is what is looked up here, so
    this table cannot be turned back into a list of health workers' phone
    numbers by anyone who obtains it. `masked` is what a screen may display.
    """

    __tablename__ = "facility_contacts"

    phone_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    masked: Mapped[str] = mapped_column(Text, nullable=False)
    # What this number may do. Reporting stock is not the same permission as
    # approving a transfer, and a stolen handset must not become an approval.
    role: Mapped[str] = mapped_column(Text, nullable=False, default="reporter")
    language: Mapped[str] = mapped_column(Text, default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            f"role IN {CONTACT_ROLES}", name="ck_facility_contacts_role"
        ),
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


class SyntheticGroundTruth(Base):
    """What the generator deliberately made wrong — spec 19.2.

    Evaluation only. The trust layer scores facilities from their own signals
    and must never see this table; if it did, the precision figure it produced
    would be a measurement of nothing. Nothing in `app/` outside
    `evaluation.py` may read it, and `evaluation.py` only compares against it
    after the scoring has already happened.

    It exists because a trust score with no ground truth cannot be reported as
    precision and recall, and "it flagged some facilities" is not a number.
    """

    __tablename__ = "synthetic_ground_truth"

    facility_id: Mapped[str] = mapped_column(
        ForeignKey("facilities.id"), primary_key=True
    )
    # 'gaming'         — reports implausibly smooth or contradictory numbers
    # 'supply_failure' — genuinely short of stock, which is not dishonesty
    label: Mapped[str] = mapped_column(Text, primary_key=True)
    seeded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvalReport(Base):
    """One run of the eval harness — spec 19.2.

    These are the numbers quoted on stage, so they are stored with the moment
    they were computed and the seed they were computed from. A number without a
    date is a claim; a number with one is a measurement.
    """

    __tablename__ = "eval_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    dataset_seed: Mapped[int | None] = mapped_column(Integer)
    sections: Mapped[dict] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


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
    """One round of federated training, and the evidence of what crossed the
    wire — spec 12.2 and 27.

    The claim this table has to survive is "no facility data leaves a state".
    That is not a slide: the aggregator writes here, every round, exactly how
    many bytes it received, the shape of every tensor, a hash of the weights,
    and the number of raw rows transmitted — which the server asserts is zero
    before the row is written, rather than simply storing a zero.
    """

    __tablename__ = "federation_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy: Mapped[str | None] = mapped_column(Text)

    global_val_mae: Mapped[float | None] = mapped_column()
    # The rule this model replaces, scored on the same held-out weeks, so the
    # chart can never flatter the model by changing what it is measured against.
    baseline_mae: Mapped[float | None] = mapped_column()
    # Per state: windows trained on, live trust, the weight that trust bought
    # it, and its own validation error.
    per_silo: Mapped[list | None] = mapped_column(JSONB)

    # The inspector's evidence: what left each silo, and what did not.
    bytes_transmitted: Mapped[int | None] = mapped_column(BigInteger)
    tensor_shapes: Mapped[list | None] = mapped_column(JSONB)
    weights_sha256: Mapped[str | None] = mapped_column(Text)
    raw_rows_transmitted: Mapped[int] = mapped_column(Integer, default=0)
    silos_reporting: Mapped[int | None] = mapped_column(Integer)

    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
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

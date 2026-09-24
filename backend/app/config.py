"""Application settings.

Two ideas hold this file together.

1. Mode switches (spec Section 1, rules 3-4): every external service has a safe
   default that needs no credentials, so the core runs with nothing configured.

2. Production refuses to start unsafely. A weak signing secret, insecure
   cookies or demo shortcuts left switched on are caught at boot, not
   discovered after launch.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmMode = Literal["mock", "live"]
MapsMode = Literal["osm", "google"]
CommsMode = Literal["simulator", "live"]
ForecastMode = Literal["burn_rate", "federated"]
Environment = Literal["development", "production"]

# Long enough for HS256, and refused by name in production.
DEV_JWT_SECRET = "dev-only-signing-secret-never-use-in-production"

# A fixed development salt, so a seeded handset and a running server agree
# without anyone configuring anything. Production refuses to use it.
DEV_PHONE_SALT = "dev-only-phone-salt-never-use-in-production"
BACKEND_ROOT = Path(__file__).resolve().parent.parent


# A host hands out whatever DSN it likes. Render's is `postgresql://`; Heroku
# and several older tools still emit `postgres://`. SQLAlchemy's async engine
# refuses both, because neither names an async driver — and it refuses them at
# import time, so the symptom is a container that crash-loops before it can log
# anything useful. Rather than depending on whoever pastes the URL into a
# dashboard getting the prefix right, the app corrects it on the way in.
#
# Drivers that cannot work asynchronously at all are corrected too. An
# explicitly chosen async driver (`psycopg`, meaning v3) is left alone: that is
# a real decision, not a mistake.
SYNC_ONLY_DRIVERS = ("psycopg2", "pg8000")


def normalise_database_url(raw: str) -> str:
    """Return `raw` as a DSN this application's async engine can open.

    Two corrections, both for the same reason — a copied connection string
    should not be able to stop a deployment from booting:

    1. the scheme gains `+asyncpg` when it names no async driver;
    2. `sslmode` in the query becomes `ssl`. SQLAlchemy forwards unrecognised
       query parameters to `asyncpg.connect()` as keyword arguments, and that
       function has no `sslmode` argument at all; `ssl` accepts the same
       values (`require`, `verify-full`, ...).

    Anything that is not a Postgres URL is returned untouched.
    """
    url = raw.strip()
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    name, _, driver = scheme.partition("+")
    if name.lower() in ("postgres", "postgresql") and (
        not driver or driver.lower() in SYNC_ONLY_DRIVERS
    ):
        scheme = "postgresql+asyncpg"
    base, sep, query = rest.partition("?")
    if query:
        # Only the key is rewritten; values are left exactly as given, because
        # a re-encoded value is a different connection string.
        query = re.sub(r"(?i)(^|&)sslmode=", r"\1ssl=", query)
    return scheme + "://" + base + sep + query


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SwasthSetu"
    api_version: str = "0.2.0"
    environment: Environment = "development"

    # --- database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/phc"
    # Cloud Run can run many requests per instance; keep the pool inside what a
    # small Cloud SQL tier allows (instances x (pool + overflow) < max_connections).
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Cancel any statement that runs longer than this. A web request that has
    # taken half a minute has already failed as far as the person waiting is
    # concerned, and on a small volume a long query is not merely slow: a sort
    # it cannot hold in work_mem spills to a temp file that grows for as long
    # as the query lives. Measured on the deployed database, one unbounded read
    # path ran past 300 seconds and left roughly 70 MB of temp behind each time.
    # 0 disables it, which is Postgres's own meaning for the setting.
    db_statement_timeout_ms: int = 30_000

    # --- auth ---
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    session_hours: int = 12
    # Firebase Hosting forwards exactly one cookie to Cloud Run, and it must be
    # named __session. Using that name keeps sign-in working whether the site
    # is reached directly on Cloud Run or through Firebase Hosting.
    session_cookie_name: str = "__session"
    # None means "secure when in production".
    cookie_secure: bool | None = None
    login_attempts_per_window: int = 8
    login_window_minutes: int = 15

    # --- demo affordances (test reports, demo accounts) ---
    demo_mode: bool = True
    # A public showcase deployment may run demo mode on purpose; it has to say
    # so explicitly, so a real deployment can never inherit it by accident.
    allow_public_demo: bool = False

    # --- web ---
    # Built frontend served by this process in production. In development the
    # Vite dev server serves the frontend instead and this directory is absent.
    static_dir: Path = BACKEND_ROOT / "static"
    # Only needed when the frontend is served from a different origin. Empty in
    # production by default: the site and API share one origin there.
    cors_origins: list[str] = Field(default_factory=list)
    public_base_url: str = ""

    # --- mode switches, safe defaults ---
    llm_mode: LlmMode = "mock"
    maps_mode: MapsMode = "osm"
    comms_mode: CommsMode = "simulator"

    # --- credentials: only read once the matching switch is flipped ---
    gemini_api_key: str = ""
    # Flash is the right size for reading a bed count and a four-character code
    # off a photograph: cheap enough to run on every ward, every day, and free
    # of the billing requirement a Pro model carries.
    #
    # Not 2.5: the API now answers 404 for gemini-2.5-flash and -flash-lite with
    # "no longer available to new users", naming 3.6-flash as the replacement.
    # Verified against a real key on 2026-09-20.
    #
    # And not 3.6-flash either, despite that advice. Measured against a real
    # key on 2026-09-23, sending the same delivery note to each: 3.6-flash
    # never answered (ReadTimeout at 158s, twice), 3.5-flash answered
    # correctly in 38.7s, and this one answered correctly in 10.1s. A model
    # that takes longer than a minute is a model that has already failed in
    # front of whoever is watching, so the fast one that works wins. Still a
    # different id from the briefing model below, which is the point of
    # having two.
    gemini_model: str = "gemini-3.1-flash-lite"
    # A *different* model from the vision one, on purpose. The free tier limits
    # GenerateRequestsPerDayPerProjectPerModel to 20 — per model — so putting
    # the briefing on its own id means a day of ward photos cannot exhaust the
    # briefings, or the reverse. Confirmed reachable and returning strict
    # {en, hi} JSON on 2026-09-22; a lite model is right for one sentence.
    # Not an alias like `gemini-flash-lite-latest`: a model that silently
    # changes under a demo is a model that can silently break one.
    gemini_text_model: str = "gemini-3.5-flash-lite"
    # How long a generated briefing stays good. It also expires early whenever
    # the stock position it describes changes — see workspace.briefing_hash.
    briefing_ttl_hours: float = 24.0
    # The whole table's ceiling. Two rows per facility is already bounded by
    # the composite primary key, but 3,510 facilities x 2 languages is 4 MB,
    # and this table is a cache: the oldest rows are evicted rather than kept.
    max_briefing_rows: int = 500
    # Voice call references. Twilio keeps the call record and any recording;
    # this table holds only enough to show a call happened. Capped for the
    # same reason every demo-facing table here is capped.
    max_call_log_rows: int = 200
    # Server key: Routes API only, restricted to this service. Never sent to a browser.
    google_maps_server_key: str = ""
    # Browser key: Map Tiles API only, restricted to the site's domains. It is
    # visible to anyone who opens the site, which is how browser map keys work;
    # the domain and API restrictions are what protect it.
    google_maps_browser_key: str = ""
    twilio_account_sid: str = ""
    # The auth token is the account's master credential AND the key Twilio
    # signs inbound webhooks with, so it is always required in live mode even
    # when an API key is used for sending.
    twilio_auth_token: str = ""
    # An API key pair is the credential Twilio recommends for sending: it is
    # scoped, revocable on its own, and rotating it does not invalidate the
    # webhook signatures the auth token verifies. Optional — sending falls
    # back to the account SID and auth token when no key is configured.
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_sms_number: str = ""
    twilio_whatsapp_number: str = ""
    twilio_voice_number: str = ""
    public_webhook_base_url: str = ""
    bhashini_api_key: str = ""
    redis_url: str = ""
    # The federation's own interpreter (torch lives there, never here). Only
    # read by the local-only "Run next round"; blank means that button is off.
    federation_python: str = ""

    # --- reorder floor thresholds (spec 12.1) ---
    critical_days: float = 3.0
    at_risk_days: float = 7.0
    burn_rate_window_days: int = 28
    bed_occupancy_escalate_pct: float = 90.0
    staff_checkin_escalate_pct: float = 50.0

    # --- redistribution constraints (spec 12.3) ---
    recipient_target_days: float = 14.0
    # A donor always keeps at least this much cover — well above the at-risk
    # line, so giving stock away never pushes a donor into trouble itself.
    donor_floor_days: float = 14.0
    road_factor: float = 1.3
    max_transfer_km: float = 150.0
    cold_chain_max_km: float = 60.0
    avg_speed_kmh: float = 35.0
    handling_hours: float = 0.5
    min_transfer_units: int = 5
    critical_cost_weight: float = 0.35

    # --- the pharmacist's workspace ---
    # Two caps, because they stop two different things. The per-facility one
    # keeps any single pharmacist's queue reviewable by the officer who has to
    # read it. The global one is the database size guard: a judge clicking
    # "Request stock" is otherwise an unbounded writer against a 1 GB volume.
    # Both count only transfers tagged 'facility_request', so the solver's own
    # proposals can never consume a pharmacist's allowance.
    max_open_requests_per_facility: int = 3
    max_open_facility_requests_global: int = 100
    # A request raised after the cutoff leaves the next morning; district
    # stores do not load vehicles at night. Both are assumptions, returned to
    # the browser alongside every estimate and shown on screen as assumptions,
    # never presented as a scheduled time.
    dispatch_cutoff_hour: int = 14          # local time, 24h clock
    working_hours_per_day: float = 8.0
    # How long a dispatched batch has to be confirmed before it reads as
    # overdue (spec 26.3). Generous on purpose: a batch sitting unconfirmed is
    # a question for the officer, not an accusation against the facility.
    receipt_window_hours: float = 72.0

    # --- bed capture (spec 26.2) ---
    # How close a photo or check-in must be to the registered coordinates. GPS
    # is metre-accurate; a cell tower covers kilometres, so the same radius
    # would either reject honest reports or accept anything.
    geofence_gps_km: float = 0.25
    geofence_cell_km: float = 2.0
    # A bed count within this fraction of the admission register is agreement;
    # registers are written by hand and a photo catches one moment of the day.
    bed_register_tolerance: float = 0.25
    # Below this the model is not sure enough to record a count unreviewed.
    bed_confidence_floor: float = 0.45

    # --- phone channels (spec 13) ---
    # Salt for hashing inbound phone numbers. Deliberately NOT derived from
    # JWT_SECRET: rotating a session secret must never orphan every registered
    # handset, and the only symptom of that would be staff being told their
    # number is not registered.
    phone_hash_salt: str = ""
    # Below this, an extracted reading is held for confirmation rather than
    # committed (spec 13, step 5).
    channel_confidence_floor: float = 0.6

    # --- forecasting (spec 27, B5) ---
    # burn_rate: days of cover from the last 28 days of readings, the rule the
    #   platform has always used and the one that needs nothing but the data.
    # federated: use the federated model's published predictions when a fresh
    #   one exists for that facility and medicine, and fall back to the burn
    #   rate whenever it does not. A bad training run degrades to today's
    #   behaviour rather than breaking the map.
    forecast_mode: ForecastMode = "burn_rate"
    # Beyond this a published forecast is stale and is ignored in favour of the
    # burn rate. A week-ahead forecast is worthless once the week has passed.
    forecast_max_age_days: float = 8.0

    @field_validator("database_url", mode="after")
    @classmethod
    def _name_the_async_driver(cls, value: str) -> str:
        return normalise_database_url(value)

    @property
    def phone_salt(self) -> str:
        """The salt actually used to hash handsets."""
        return self.phone_hash_salt or DEV_PHONE_SALT

    @property
    def site_root(self) -> Path:
        """Where the built website is expected to be."""
        return self.static_dir.resolve()

    @property
    def serves_built_site(self) -> bool:
        """Whether a built website is actually present to serve.

        In development its absence is normal — Vite serves the site and this
        process serves only the API. In a container it means the image was
        built without the frontend, and every page request will 404 while the
        API keeps answering, which is exactly the shape of failure that hides
        itself. `/api/health` reports this so it cannot hide.
        """
        return (self.site_root / "index.html").is_file()

    @property
    def site_diagnosis(self) -> str | None:
        """Why the website is not being served, when it is not.

        None when it is. Otherwise a short phrase that distinguishes the three
        different faults — wrong path, empty copy, missing entry file — without
        printing the path itself, because this is reported on a public endpoint.
        """
        if self.serves_built_site:
            return None
        root = self.site_root
        if not root.is_dir():
            return "the static directory does not exist"
        entries = sum(1 for _ in root.iterdir())
        if entries == 0:
            return "the static directory exists but is empty"
        return "the static directory holds {0} entries but no index.html".format(entries)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def comms_credential_problems(self) -> list[str]:
        """What is missing before COMMS_MODE=live can do anything real.

        Returned rather than raised so the same list serves the production
        start-up guard and the /api/comms/status endpoint, which tells a
        reader on screen exactly which credential is absent instead of
        letting the first send fail with a 401 nobody sees.
        """
        if self.comms_mode != "live":
            return []
        missing: list[str] = []
        if not self.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID is required when COMMS_MODE=live")
        if not self.twilio_auth_token:
            missing.append(
                "TWILIO_AUTH_TOKEN is required when COMMS_MODE=live: it is what "
                "inbound webhook signatures are verified against"
            )
        if not (self.twilio_sms_number or self.twilio_whatsapp_number):
            missing.append(
                "TWILIO_SMS_NUMBER or TWILIO_WHATSAPP_NUMBER is required when "
                "COMMS_MODE=live"
            )
        # A half-configured key pair would silently fall back to the auth
        # token, which is not what anyone setting one of them intended.
        if bool(self.twilio_api_key_sid) != bool(self.twilio_api_key_secret):
            missing.append(
                "TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET must be set together"
            )
        return missing

    @property
    def twilio_send_auth(self) -> tuple[str, str]:
        """The credential pair used to authenticate a send.

        The API key when one is configured, the account credentials otherwise.
        Twilio accepts either as HTTP basic auth on the same endpoint.
        """
        if self.twilio_api_key_sid and self.twilio_api_key_secret:
            return self.twilio_api_key_sid, self.twilio_api_key_secret
        return self.twilio_account_sid, self.twilio_auth_token

    @property
    def uses_external_services(self) -> bool:
        return (
            self.llm_mode == "live"
            or self.maps_mode == "google"
            or self.comms_mode == "live"
        )

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> "Settings":
        if not self.is_production:
            return self
        problems: list[str] = []
        if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to a random value of at least 32 characters")
        if self.cookie_secure is False:
            problems.append("COOKIE_SECURE cannot be false in production")
        if self.demo_mode and not self.allow_public_demo:
            problems.append(
                "DEMO_MODE is on. Set DEMO_MODE=false, or ALLOW_PUBLIC_DEMO=true "
                "if this is deliberately a public demo deployment"
            )
        if not self.phone_hash_salt or self.phone_hash_salt == DEV_PHONE_SALT:
            problems.append(
                "PHONE_HASH_SALT must be set to a random value: it is what stops this "
                "database from being turned back into a list of health workers' phone numbers"
            )
        if "postgres:postgres@" in self.database_url:
            problems.append("DATABASE_URL is still using the development credentials")
        problems.extend(self.comms_credential_problems)
        if problems:
            raise ValueError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

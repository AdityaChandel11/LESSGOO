"""Application settings.

Two ideas hold this file together.

1. Mode switches (spec Section 1, rules 3-4): every external service has a safe
   default that needs no credentials, so the core runs with nothing configured.

2. Production refuses to start unsafely. A weak signing secret, insecure
   cookies or demo shortcuts left switched on are caught at boot, not
   discovered after launch.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmMode = Literal["mock", "live"]
MapsMode = Literal["osm", "google"]
CommsMode = Literal["simulator", "live"]
ForecastMode = Literal["burn_rate", "federated"]
Environment = Literal["development", "production"]

# Long enough for HS256, and refused by name in production.
DEV_JWT_SECRET = "dev-only-signing-secret-never-use-in-production"
BACKEND_ROOT = Path(__file__).resolve().parent.parent


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
    gemini_model: str = "gemini-3.6-flash"
    # Server key: Routes API only, restricted to this service. Never sent to a browser.
    google_maps_server_key: str = ""
    # Browser key: Map Tiles API only, restricted to the site's domains. It is
    # visible to anyone who opens the site, which is how browser map keys work;
    # the domain and API restrictions are what protect it.
    google_maps_browser_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_sms_number: str = ""
    twilio_whatsapp_number: str = ""
    twilio_voice_number: str = ""
    public_webhook_base_url: str = ""
    bhashini_api_key: str = ""
    redis_url: str = ""

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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

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
        if "postgres:postgres@" in self.database_url:
            problems.append("DATABASE_URL is still using the development credentials")
        if problems:
            raise ValueError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

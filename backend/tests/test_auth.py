"""Access policy, sessions, and production start-up safety."""

import jwt
import pytest

from datetime import datetime, timedelta, timezone

from app.auth import (
    FailureWindow,
    LimitRule,
    Principal,
    can_decide_transfer,
    can_plan_state,
    can_submit_reading,
    create_session_token,
    hash_password,
    login_retry_after,
    verify_password,
)
from app.config import Settings, settings
from app.models import User


def person(role, state=None, district=None, facility=None):
    return Principal(1, "p@example.org", "P", role, state, district, facility)


ADMIN = person("admin")
MH_OFFICER = person("state_officer", "MH")
NASHIK_MO = person("block_mo", "MH", "Nashik")
PHARMACIST = person("facility_user", "MH", "Nashik", "HFR-MH-PHC-00001")


# ------------------------------------------------------------- planning ---

@pytest.mark.parametrize(
    "who, state, allowed",
    [
        (ADMIN, "MH", True),
        (MH_OFFICER, "MH", True),
        (MH_OFFICER, "KL", False),
        (NASHIK_MO, "MH", False),
        (PHARMACIST, "MH", False),
    ],
)
def test_who_can_generate_a_state_plan(who, state, allowed):
    assert can_plan_state(who, state) is allowed


# ------------------------------------------------------------ decisions ---

WITHIN_NASHIK = dict(from_state="MH", from_district="Nashik", to_state="MH", to_district="Nashik")
NASHIK_TO_PUNE = dict(from_state="MH", from_district="Nashik", to_state="MH", to_district="Pune")
CROSS_STATE = dict(from_state="MH", from_district="Nashik", to_state="GJ", to_district="Surat")


@pytest.mark.parametrize(
    "who, route, allowed",
    [
        (ADMIN, CROSS_STATE, True),
        (MH_OFFICER, WITHIN_NASHIK, True),
        (MH_OFFICER, NASHIK_TO_PUNE, True),
        # A state officer's authority stops at the state border.
        (MH_OFFICER, CROSS_STATE, False),
        (NASHIK_MO, WITHIN_NASHIK, True),
        # A district officer cannot send stock out of, or into, another district.
        (NASHIK_MO, NASHIK_TO_PUNE, False),
        (PHARMACIST, WITHIN_NASHIK, False),
    ],
)
def test_who_can_decide_a_transfer(who, route, allowed):
    assert can_decide_transfer(who, **route) is allowed


# -------------------------------------------------------------- reports ---

OWN = dict(facility_id="HFR-MH-PHC-00001", facility_state="MH", facility_district="Nashik")
NEIGHBOUR = dict(facility_id="HFR-MH-PHC-00002", facility_state="MH", facility_district="Nashik")
PUNE = dict(facility_id="HFR-MH-PHC-00099", facility_state="MH", facility_district="Pune")


@pytest.mark.parametrize(
    "who, facility, allowed",
    [
        (PHARMACIST, OWN, True),
        (PHARMACIST, NEIGHBOUR, False),
        (NASHIK_MO, NEIGHBOUR, True),
        (NASHIK_MO, PUNE, False),
        (MH_OFFICER, PUNE, True),
        (ADMIN, PUNE, True),
    ],
)
def test_who_can_report_stock(who, facility, allowed):
    assert can_submit_reading(who, **facility) is allowed


# ------------------------------------------------------------ passwords ---

def test_passwords_are_hashed_and_verified():
    h = hash_password("a-long-enough-password")
    assert h.startswith("$argon2id$")
    assert verify_password("a-long-enough-password", h)
    assert not verify_password("wrong-password-entirely", h)


def test_short_passwords_are_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


def test_verifying_against_garbage_hash_fails_closed():
    assert verify_password("anything-at-all-here", "not-a-hash") is False


# -------------------------------------------------------------- sessions ---

def test_session_token_carries_identity_and_expiry():
    user = User(id=42, email="a@b.c", name="A", role="state_officer", password_hash="x")
    claims = jwt.decode(
        create_session_token(user), settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    assert claims["sub"] == "42"
    assert claims["exp"] > claims["iat"]


def test_token_signed_with_another_secret_is_rejected():
    forged = jwt.encode({"sub": "1", "iat": 0, "exp": 9999999999}, "attacker-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(forged, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---------------------------------------------------------- rate limiting ---

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
WINDOW = timedelta(minutes=15)
RULES = (LimitRule("pair", 3), LimitRule("email", 9), LimitRule("ip", 24))


def windows(pair=0, email=0, ip=0, oldest_minutes_ago=5):
    oldest = NOW - timedelta(minutes=oldest_minutes_ago)
    return {
        "pair": FailureWindow(pair, oldest if pair else None),
        "email": FailureWindow(email, oldest if email else None),
        "ip": FailureWindow(ip, oldest if ip else None),
    }


def test_sign_in_allowed_under_every_limit():
    assert login_retry_after(windows(pair=2, email=8, ip=23), NOW, WINDOW, RULES) is None


def test_one_address_guessing_one_account_is_locked_out():
    wait = login_retry_after(windows(pair=3, email=3, ip=3), NOW, WINDOW, RULES)
    assert wait == 10 * 60  # oldest failure 5 min ago, 15 min window


def test_account_limit_holds_even_when_the_address_keeps_changing():
    # A client rotating forged IPs never trips the pair or ip limits, but the
    # per-account limit ignores addresses entirely.
    assert login_retry_after(windows(pair=1, email=9, ip=1), NOW, WINDOW, RULES) is not None


def test_one_address_spraying_many_accounts_is_locked_out():
    assert login_retry_after(windows(pair=1, email=1, ip=24), NOW, WINDOW, RULES) is not None


def test_lockout_ends_when_the_oldest_failure_leaves_the_window():
    assert login_retry_after(windows(pair=3, oldest_minutes_ago=14.99), NOW, WINDOW, RULES) == 1


# ------------------------------------------------------ production safety ---

GOOD_PROD = dict(
    environment="production",
    jwt_secret="x" * 48,
    # A real deployment must carry its own phone salt: the registry is
    # unreadable without the one that hashed it.
    phone_hash_salt="y" * 40,
    demo_mode=False,
    database_url="postgresql+asyncpg://app:s3cret@/phc?host=/cloudsql/p:r:i",
)


def test_valid_production_settings_start():
    assert Settings(**GOOD_PROD).secure_cookies is True


@pytest.mark.parametrize(
    "override, problem",
    [
        (dict(jwt_secret="dev-only-change-me"), "JWT_SECRET"),
        (dict(jwt_secret="short"), "JWT_SECRET"),
        (dict(demo_mode=True), "DEMO_MODE"),
        (dict(cookie_secure=False), "COOKIE_SECURE"),
        (dict(database_url="postgresql+asyncpg://postgres:postgres@localhost/phc"), "DATABASE_URL"),
        # Without its own salt, every registered handset is orphaned and the
        # only symptom is staff being told their number is unknown.
        (dict(phone_hash_salt=""), "PHONE_HASH_SALT"),
        (dict(phone_hash_salt="dev-only-phone-salt-never-use-in-production"), "PHONE_HASH_SALT"),
    ],
)
def test_unsafe_production_settings_refuse_to_start(override, problem):
    with pytest.raises(ValueError, match=problem):
        Settings(**{**GOOD_PROD, **override})


def test_public_demo_must_be_opted_into_explicitly():
    assert Settings(**{**GOOD_PROD, "demo_mode": True, "allow_public_demo": True}).demo_mode

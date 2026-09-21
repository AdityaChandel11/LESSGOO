"""Deployment configuration that must not silently drift."""

import inspect
import json
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.dialects.postgresql import asyncpg as sa_asyncpg
from sqlalchemy.engine import make_url

from app.api import HealthOut
from app.config import Settings, normalise_database_url, settings
from app.main import CSP

REPO_ROOT = Path(__file__).resolve().parents[2]

# The smallest settings that production will actually accept, so a test about
# production behaviour is not really a test about the refusal rules.
PROD_MINIMUM = dict(
    environment="production",
    jwt_secret="x" * 48,
    phone_hash_salt="y" * 40,
    demo_mode=False,
    database_url="postgresql+asyncpg://app:s3cret@db.internal/phc",
)


def _firebase_headers() -> dict[str, str]:
    config = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))
    catch_all = next(h for h in config["hosting"]["headers"] if h["source"] == "**")
    return {h["key"]: h["value"] for h in catch_all["headers"]}


def test_firebase_hosting_serves_the_same_security_policy_as_the_api():
    assert _firebase_headers()["Content-Security-Policy"] == CSP


def test_firebase_rewrites_the_api_to_cloud_run_and_everything_else_to_the_app():
    rewrites = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))["hosting"]["rewrites"]
    assert rewrites[0]["source"] == "/api/**" and "run" in rewrites[0]
    assert rewrites[-1] == {"source": "**", "destination": "/index.html"}


def test_session_cookie_name_survives_firebase_hosting():
    # Firebase Hosting strips every cookie except __session before a request
    # reaches Cloud Run; any other name would sign everyone out on each request.
    assert settings.session_cookie_name == "__session"


# --- DATABASE_URL, as a host actually hands it over ----------------------
#
# Render's dashboard shows `postgresql://`; Heroku-era tooling still shows
# `postgres://`. SQLAlchemy's async engine rejects both at import time, so
# getting this wrong is not a degraded feature, it is a container that never
# starts. These tests exist because that failure is invisible until deploy.

@pytest.mark.parametrize(
    "given,expected",
    [
        # what Render's dashboard shows
        ("postgresql://u:p@dpg-x:5432/db", "postgresql+asyncpg://u:p@dpg-x:5432/db"),
        # what Heroku and older tools show
        ("postgres://u:p@dpg-x:5432/db", "postgresql+asyncpg://u:p@dpg-x:5432/db"),
        # already correct: left exactly alone
        ("postgresql+asyncpg://u:p@dpg-x/db", "postgresql+asyncpg://u:p@dpg-x/db"),
        # a sync-only driver cannot serve an async engine under any setting
        ("postgresql+psycopg2://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # psycopg v3 is async-capable, so a deliberate choice stands
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        # case from a hand-typed value
        ("POSTGRESQL://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # a stray newline from copy-paste
        ("postgresql://u:p@h/db" + chr(10), "postgresql+asyncpg://u:p@h/db"),
        # sslmode is renamed, and nothing else in the query is touched
        (
            "postgresql://u:p@h/db?sslmode=require&application_name=swasthsetu",
            "postgresql+asyncpg://u:p@h/db?ssl=require&application_name=swasthsetu",
        ),
        ("postgresql://u:p@h/db?ssl=verify-full", "postgresql+asyncpg://u:p@h/db?ssl=verify-full"),
        # a socket path in the query survives unencoded
        (
            "postgresql://app:pw@/phc?host=/cloudsql/p:r:i",
            "postgresql+asyncpg://app:pw@/phc?host=/cloudsql/p:r:i",
        ),
        # not Postgres, not our business
        ("sqlite+aiosqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
        ("", ""),
    ],
)
def test_database_url_is_corrected_to_an_async_driver(given, expected):
    assert normalise_database_url(given) == expected


def test_a_password_containing_a_scheme_like_string_is_not_mangled():
    # Passwords are generated, not chosen, and can contain anything. Only the
    # scheme and the query keys may be rewritten.
    ugly = "postgresql://u:sslmode=a?b@h/db"
    assert normalise_database_url(ugly) == "postgresql+asyncpg://u:sslmode=a?b@h/db"


def test_settings_applies_the_correction_before_anything_reads_the_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dpg-x:5432/db")
    assert Settings().database_url == "postgresql+asyncpg://u:p@dpg-x:5432/db"


def test_the_corrected_url_produces_arguments_asyncpg_will_actually_accept():
    # The real proof, and the one a string comparison cannot give: SQLAlchemy
    # forwards leftover query parameters straight into asyncpg.connect() as
    # keyword arguments, so an unrecognised one is a TypeError on first
    # connection rather than a warning.
    url = make_url(normalise_database_url("postgresql://u:p@h:5432/db?sslmode=require"))
    assert url.get_driver_name() == "asyncpg"
    _, kwargs = sa_asyncpg.dialect().create_connect_args(url)
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    assert not set(kwargs) - accepted, "asyncpg.connect() would reject these"


def test_the_committed_default_names_the_async_driver():
    assert Settings.model_fields["database_url"].default.startswith("postgresql+asyncpg://")


# --- a deployment that serves the API but no website ---------------------
#
# This is the one deployment fault that looks healthy from every other angle:
# /api/health answers, the host's health check passes, and every page 404s.
# It cost a live demo once, so it is reported rather than logged.

def test_a_built_site_is_detected_and_diagnosed(tmp_path):
    site = tmp_path / "static"
    absent = Settings(static_dir=site)
    assert absent.serves_built_site is False
    assert absent.site_diagnosis == "the static directory does not exist"

    site.mkdir()
    empty = Settings(static_dir=site)
    assert empty.serves_built_site is False
    assert empty.site_diagnosis == "the static directory exists but is empty"

    (site / "assets").mkdir()
    partial = Settings(static_dir=site)
    assert partial.serves_built_site is False
    assert "no index.html" in partial.site_diagnosis

    (site / "index.html").write_text("<!doctype html>", encoding="utf-8")
    built = Settings(static_dir=site)
    assert built.serves_built_site is True
    assert built.site_diagnosis is None


def test_health_reports_whether_the_website_is_being_served():
    fields = HealthOut.model_fields
    assert "site_served" in fields and fields["site_served"].annotation is bool
    assert "site_detail" in fields


def test_health_does_not_hand_a_filesystem_path_to_the_public_in_production():
    # /api/health needs no credentials, so the resolved path is development-only.
    prod = Settings(**PROD_MINIMUM, static_dir="/app/static")
    assert prod.is_production
    payload = HealthOut(
        status="ok",
        app="x",
        version="0",
        environment=prod.environment,
        demo_mode=prod.demo_mode,
        database=True,
        modes={},
        external_services_in_use=False,
        site_served=prod.serves_built_site,
        site_detail=prod.site_diagnosis,
        site_root=None if prod.is_production else str(prod.site_root),
    )
    assert payload.site_root is None


def test_the_dockerfile_refuses_to_build_without_the_website():
    # The guard is what turns a silent 404 into a failed build.
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=web /web/dist ./static" in dockerfile
    guard = next(
        (line for line in dockerfile.splitlines() if line.startswith("RUN test -f")), ""
    )
    assert "/app/static/index.html" in guard and "exit 1" in guard

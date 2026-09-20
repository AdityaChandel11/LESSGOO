"""Deployment configuration that must not silently drift."""

import inspect
import json
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.dialects.postgresql import asyncpg as sa_asyncpg
from sqlalchemy.engine import make_url

from app.config import Settings, normalise_database_url, settings
from app.main import CSP

REPO_ROOT = Path(__file__).resolve().parents[2]


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

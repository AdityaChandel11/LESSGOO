"""Deployment configuration that must not silently drift."""

import json
from pathlib import Path

from app.config import settings
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

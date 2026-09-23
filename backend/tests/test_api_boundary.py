"""Enforce the credential boundary that api.py's docstring only describes.

Spec Section 1, rule 3: the app must run, seed and demo with every credential
blank, which holds only while each external service is reached through exactly
one mode-switched adapter. Until now that rule was a comment, and a comment
cannot fail a build. These tests read the source and do.

Four things are enforced:
  * `app/api.py` imports no vendor SDK and calls no vendor host directly;
  * every credential in settings is read only by the module that owns it;
  * no module outside those owners touches one, however it is spelled;
  * the set of routes that answer without a session is exactly the declared one.

When a new adapter legitimately needs a credential, add it to CREDENTIAL_OWNERS
in the same commit. That edit is the review: it is a one-line diff that says a
new file is now allowed to hold a key.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import Settings

APP_DIR = Path(__file__).resolve().parent.parent / "app"
API = APP_DIR / "api.py"

# Modules that may read each credential, and nothing else may.
CREDENTIAL_OWNERS: dict[str, set[str]] = {
    "jwt_secret": {"auth.py"},
    "gemini_api_key": {"vision.py"},
    "google_maps_server_key": {"maps.py"},
    # The browser key is not secret: it is sent to every visitor, restricted by
    # HTTP referrer, and the client-config endpoint is how it gets there.
    "google_maps_browser_key": {"maps.py", "api.py"},
    # The phone salt belongs to the ingestion spine: it is the only thing that
    # hashes an inbound number, and `phone_salt` is the property it reads.
    "phone_hash_salt": {"ingest.py"},
    "phone_salt": {"ingest.py"},
    "twilio_account_sid": {"comms.py"},
    # comms.py is the only module allowed to hold a Twilio credential, and
    # the auth token is what proves an inbound webhook really came from
    # Twilio. Validation is implemented there by hand rather than through
    # the SDK, so it can be unit-tested with a known token and no account.
    "twilio_auth_token": {"comms.py"},
    "bhashini_api_key": set(),
}

# Vendor SDKs and transports that belong behind an adapter, never in api.py.
FORBIDDEN_IMPORTS = {
    "google",
    "google.genai",
    "google.generativeai",
    "googleapiclient",
    "twilio",
    "openai",
    "anthropic",
    "boto3",
    "httpx",
    "requests",
    "aiohttp",
    "urllib.request",
}

VENDOR_HOSTS = (
    "generativelanguage.googleapis.com",
    "routes.googleapis.com",
    "tile.googleapis.com",
    "maps.googleapis.com",
    "api.twilio.com",
    "bhashini",
)

CREDENTIAL_WORDS = ("key", "token", "secret", "salt", "sid", "password", "credential")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update("{0}.{1}".format(node.module, a.name) for a in node.names)
    return found


def _settings_reads(tree: ast.Module) -> set[str]:
    """Every `settings.<field>` this module reads."""
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
        ):
            reads.add(node.attr)
    return reads


def _string_literals(tree: ast.Module) -> list[str]:
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_credential_owners_covers_every_credential_in_settings() -> None:
    """A new credential cannot be added to settings without a decision here."""
    credentials = {
        name
        for name in Settings.model_fields
        if any(word in name for word in CREDENTIAL_WORDS)
        # phone-number salts and hashes arrive with the phone channels; list them
        # here when they do.
    }
    missing = credentials - set(CREDENTIAL_OWNERS)
    assert not missing, (
        "settings gained credential field(s) {0} with no owner declared in "
        "CREDENTIAL_OWNERS — decide which single module may read them".format(sorted(missing))
    )


def test_api_imports_no_vendor_sdk_or_http_client() -> None:
    imported = _imported_modules(_tree(API))
    offenders = sorted(imported & FORBIDDEN_IMPORTS)
    assert not offenders, (
        "app/api.py imports {0}. An external service must be reached through its "
        "mode-switched adapter (vision, maps, comms), where the fallback and its "
        "tests live.".format(offenders)
    )


def test_api_names_no_vendor_host() -> None:
    literals = " ".join(_string_literals(_tree(API))).lower()
    offenders = [host for host in VENDOR_HOSTS if host in literals]
    assert not offenders, (
        "app/api.py mentions vendor endpoint(s) {0}; that call belongs in the adapter".format(offenders)
    )


@pytest.mark.parametrize("module", sorted(p.name for p in APP_DIR.glob("*.py")))
def test_only_the_owning_module_reads_a_credential(module: str) -> None:
    if module == "config.py":
        return  # config defines them; that is its job
    reads = _settings_reads(_tree(APP_DIR / module))
    for field, owners in CREDENTIAL_OWNERS.items():
        if field in reads and module not in owners:
            pytest.fail(
                "{0} reads settings.{1}. Allowed: {2}. Move the call into the adapter, or "
                "add this module to CREDENTIAL_OWNERS deliberately.".format(
                    module, field, sorted(owners) or "no module yet"
                )
            )


@pytest.mark.parametrize("module", sorted(p.name for p in APP_DIR.glob("*.py")))
def test_no_module_reads_an_undeclared_credential_shaped_field(module: str) -> None:
    """Catches a credential read that CREDENTIAL_OWNERS has not heard of."""
    if module == "config.py":
        return
    suspicious = {
        name
        for name in _settings_reads(_tree(APP_DIR / module))
        if any(word in name for word in CREDENTIAL_WORDS)
    }
    undeclared = {name for name in suspicious if name not in CREDENTIAL_OWNERS}
    assert not undeclared, (
        "{0} reads credential-shaped setting(s) {1} that CREDENTIAL_OWNERS does not "
        "cover".format(module, sorted(undeclared))
    )


def test_api_docstring_still_states_the_rule() -> None:
    """The docstring is no longer the enforcement, but it must not drift from it."""
    doc = ast.get_docstring(_tree(API)) or ""
    assert "mode-switched adapter" in doc, (
        "app/api.py's docstring no longer states the boundary these tests enforce"
    )


# ---------------------------------------------------------------------------
# The unauthenticated surface
# ---------------------------------------------------------------------------
# `public_router` is the only part of api.py that answers without a session, so
# what sits on it is an access-control decision rather than a routing detail.
# The landing page needs national aggregates before anyone has signed in; it
# must not be the reason district rollups or individual facilities quietly
# become public too. Adding a route here is a one-line diff that says so.
PUBLIC_ROUTES = {
    ("GET", "/client-config"),
    ("GET", "/health"),
    ("GET", "/map/summary"),
    ("GET", "/map/states"),
    # The three inbound webhooks. Twilio carries no session, so these cannot
    # sit behind one — but they are not open: every one of them validates the
    # provider signature before it does anything at all, and with no auth
    # token configured that check refuses everything. They are the only
    # unauthenticated *write* routes in the system, which is why they are
    # listed here individually rather than by prefix.
    ("POST", "/webhooks/sms"),
    ("POST", "/webhooks/whatsapp"),
    ("POST", "/webhooks/voice/status"),
}


def test_public_router_carries_exactly_the_declared_routes() -> None:
    from app.api import public_router

    actual = {
        (method, route.path)
        for route in public_router.routes
        for method in getattr(route, "methods", set())
        if method != "HEAD"
    }
    assert actual == PUBLIC_ROUTES, (
        "the unauthenticated surface changed. Added {0}, removed {1}. Every path "
        "here answers a stranger, so update PUBLIC_ROUTES deliberately.".format(
            sorted(actual - PUBLIC_ROUTES) or "nothing",
            sorted(PUBLIC_ROUTES - actual) or "nothing",
        )
    )


def test_facility_detail_stays_behind_a_session() -> None:
    """The public aggregates stop at state level; detail needs a sign-in."""
    from app.api import router

    guarded = {route.path for route in router.routes}
    for path in ("/map/districts", "/map/facilities", "/facilities/{facility_id}"):
        assert path in guarded, "{0} left the authenticated router".format(path)

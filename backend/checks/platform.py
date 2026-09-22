"""Does the platform stand up on its own, with no credentials and no trust?

This is the check that used to be called "production_check": it drives the real
app and asserts the things a hostile visitor would probe first — that nothing
answers without a session, that roles are enforced rather than merely modelled,
that the security headers are actually on the response, that sign-in cannot be
brute-forced, and that the whole thing runs with every external credential
blank.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import delete, select

from app import childproc
from app.config import settings
from app.models import LoginFailure

from .harness import Checker, Report, client, db, demo_emails

FAKE_EMAIL = "chk-nobody@demo.swasthsetu.in"
BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _blank_env_defaults() -> dict:
    """Settings as they are with no .env anywhere: run from a directory without one.

    The database URL is passed explicitly, so the probe does not depend on the
    default DSN happening to work on this machine.
    """
    from app.config import settings as current

    scratch = tempfile.mkdtemp(prefix="swasthsetu-blank-")
    # The env below is deliberately almost empty — this check exists to prove
    # the app starts with no configuration at all — so childproc.run merges in
    # the UTF-8 variables and refills nothing else.
    proc = childproc.run(
        [sys.executable, "-m", "checks.blankenv"],
        cwd=scratch,
        env={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "PYTHONPATH": str(BACKEND_ROOT),
            "PYTHONIOENCODING": "utf-8",
            "DATABASE_URL": current.database_url,
        },
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip().splitlines()[-1])
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def run() -> Report:
    c = Checker("platform")

    async with client() as http:
        r = await http.get("/api/health")
        body = r.json() if r.status_code == 200 else {}
        c.eq("health answers without a session", r.status_code, 200)
        c.eq("database reachable", body.get("database"), True)

        modes = body.get("modes") or {}
        c.ok(
            "health reports which modes are running",
            {"llm", "maps", "comms"} <= set(modes),
            "llm={0} maps={1} comms={2}".format(
                modes.get("llm"), modes.get("maps"), modes.get("comms")
            ),
        )

    # Rule 3 of the spec: the app must run with every credential blank. Asserting
    # that against *this* process would pass for the wrong reason the moment a
    # real key is configured, so it is asked of a process that has no .env to
    # read at all.
    blank = _blank_env_defaults()
    c.eq("with no configuration at all, the LLM is mocked", blank["llm_mode"], "mock")
    c.eq("the basemap needs no key", blank["maps_mode"], "osm")
    c.eq("messaging is simulated", blank["comms_mode"], "simulator")
    c.eq("no external service is reached", blank["uses_external_services"], False)
    c.ok(
        "and no credential is baked into the defaults",
        not (blank["gemini_key_set"] or blank["maps_server_key_set"] or blank["twilio_set"]),
        "gemini={0} maps={1} twilio={2}".format(
            blank["gemini_key_set"], blank["maps_server_key_set"], blank["twilio_set"]
        ),
    )

    async with client() as http:

        cfg = await http.get("/api/client-config")
        c.eq("client config is public", cfg.status_code, 200)
        c.ok(
            "no server-side key reaches the browser",
            cfg.json().get("maps_browser_key", "") == "",
            "browser key is only sent once MAPS_MODE=google",
        )

        # Nothing else answers unauthenticated.
        for path in ("/api/skus", "/api/facilities", "/api/movements", "/api/trust/queue"):
            r = await http.get(path)
            c.eq("{0} refuses anonymous access".format(path), r.status_code, 401)

        c.ok(
            "security headers on the response",
            r.headers.get("X-Frame-Options") == "DENY"
            and "Content-Security-Policy" in r.headers
            and r.headers.get("X-Content-Type-Options") == "nosniff",
            "frame-options {0}, csp {1}".format(
                r.headers.get("X-Frame-Options"), "Content-Security-Policy" in r.headers
            ),
        )

        emails = await demo_emails(http)
        c.at_least("demo accounts offered for sign-in", len(emails), 4)

    # Role scoping, through the real endpoints rather than the model.
    async with client(emails["facility_user"]) as http:
        me = (await http.get("/api/auth/me")).json()["user"]
        c.eq("signed in as the facility user", me.get("role"), "facility_user")

        others = await http.get("/api/facilities")
        c.eq("facility user may read the map", others.status_code, 200)

        foreign = next(
            (f["id"] for f in others.json() if f["id"] != me.get("facility_id")), None
        )
        if foreign:
            r = await http.post(
                "/api/stock/readings",
                json={
                    "facility_id": foreign,
                    "sku_code": "ORS",
                    "qty_on_hand": 10,
                    "source": "form",
                },
            )
            c.eq("cannot report stock for another facility", r.status_code, 403)

        logout = await http.post("/api/auth/logout")
        c.eq("logout accepted", logout.status_code, 204)
        after = await http.get("/api/skus")
        c.eq("session is over after logout", after.status_code, 401)

    async with client(emails["state_officer"]) as http:
        me = (await http.get("/api/auth/me")).json()["user"]
        own = me.get("state_silo")
        foreign_state = "KL" if own != "KL" else "MH"
        r = await http.post("/api/transfers/plan", json={"state": foreign_state})
        c.eq("cannot plan another state's transfers", r.status_code, 403)

        # Live updates: the durable cursor, not a stream.
        first = await http.get("/api/events")
        c.eq("event log answers", first.status_code, 200)
        cursor = first.json().get("cursor")
        c.ok("cursor returned", isinstance(cursor, int), "cursor={0!r}".format(cursor))
        again = await http.get("/api/events", params={"after": cursor})
        c.eq("polling past the cursor returns nothing older", again.json().get("events"), [])
        c.at_least("cursor does not go backwards", again.json().get("cursor"), cursor)

    # Sign-in cannot be ground down. A fake address is used so no real demo
    # account gets locked out by running the checks.
    async with client() as http:
        codes = []
        for _ in range(settings.login_attempts_per_window + 2):
            r = await http.post("/api/auth/login", json={"email": FAKE_EMAIL, "password": "wrong-on-purpose"})
            codes.append(r.status_code)
        c.ok(
            "sign-in is rate limited",
            429 in codes,
            "saw {0} after {1} attempts".format(sorted(set(codes)), len(codes)),
        )

    async with db() as session:
        await session.execute(delete(LoginFailure).where(LoginFailure.email == FAKE_EMAIL))
        await session.commit()
        left = (
            await session.execute(select(LoginFailure).where(LoginFailure.email == FAKE_EMAIL))
        ).first()
        c.ok("check cleaned up its own failed attempts", left is None)

    from app.main import app

    c.eq(
        "api docs follow the environment",
        app.docs_url,
        None if settings.is_production else "/api/docs",
    )
    if not settings.is_production:
        c.note(
            "running in development: production boot itself cannot be checked here until "
            "DATABASE_URL carries a real swasthsetu_app password (SPEC_DIGEST Phase E)"
        )
    return c.report

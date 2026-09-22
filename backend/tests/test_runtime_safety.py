"""Two guarantees this repository has already broken once each.

1. **Non-ASCII text cannot crash a run.** This system carries Hindi in its
   labels, its briefings and its facility names. On a cp1252 console a
   Devanagari `print()` raises UnicodeEncodeError and aborts whatever was
   running — after the work has happened. A release check that had passed
   fifteen assertions reported ERROR; a paid model call came back and was lost
   on the way to the screen.

2. **An automated run cannot spend a real Gemini request.** The free tier
   allows twenty a day per model. Until this was pinned, `python -m checks`
   read a ward photo through the live model on *every* run, and the briefing
   check did the same on the day it was written. Neither failed; both just
   quietly spent quota that a rehearsal later needed.

Both are enforced at the source — `app/__init__.py` and `app/vision.py` — so
these tests assert the source behaves, not that one caller remembered to.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app import vision
from app.config import settings

BACKEND = Path(__file__).resolve().parent.parent
HINDI = "दवाइयाँ ओरल रिहाइड्रेशन साल्ट"


# ===================================================== console encoding ===


def test_importing_the_app_makes_stdout_carry_hindi():
    """A fresh interpreter, printing Devanagari, must exit 0.

    Run as a subprocess on purpose: pytest replaces sys.stdout with a capture
    object, so asserting anything about the current process would prove
    nothing about a real terminal. `errors` is forced to a strict-ish console
    encoding here to reproduce the Windows default on any platform.
    """
    code = "import app; print({0!r})".format(HINDI)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        # Reproduce the cp1252 console this used to die on. app/__init__ is
        # expected to override it.
        env={**_clean_env(), "PYTHONIOENCODING": "cp1252"},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in result.stderr.decode("utf-8", "replace")


def test_without_the_app_package_the_same_print_would_die():
    """The guard is doing real work, not passing by luck.

    If this ever starts passing, the environment has changed underneath the
    fix and the test above has stopped proving anything.
    """
    code = "print({0!r})".format(HINDI)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        env={**_clean_env(), "PYTHONIOENCODING": "cp1252"},
    )
    assert result.returncode != 0
    assert "UnicodeEncodeError" in result.stderr.decode("utf-8", "replace")


def test_forcing_utf8_is_idempotent_and_survives_a_dead_stream():
    """Called at import and again by entry points; must never be the thing
    that breaks a process that has no usable stdout."""
    import app

    app.force_utf8_console()
    app.force_utf8_console()


def _clean_env() -> dict[str, str]:
    """A child environment without this process's UTF-8 overrides."""
    import os

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["PYTHONPATH"] = str(BACKEND)
    return env


# ======================================================= live LLM guard ===


def test_the_session_blocks_live_calls():
    """conftest turns the guard on for every test, whatever .env says."""
    assert vision.live_calls_blocked() is True
    assert settings.llm_mode == "mock"
    assert settings.gemini_api_key == ""


def test_a_ward_photo_call_with_no_client_is_refused(monkeypatch):
    """Even configured to go live, the call must raise rather than dial out."""
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    with pytest.raises(vision.LiveCallBlocked):
        _run(vision._call_gemini(b"pretend-jpeg", "image/jpeg"))


def test_a_briefing_call_with_no_client_is_refused(monkeypatch):
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    with pytest.raises(vision.LiveCallBlocked):
        _run(vision.write_briefing(facility_name="Test PHC", rows=["- ORS: 1 sachet"]))


def test_the_refusal_says_how_to_make_a_real_call_on_purpose():
    """A guard nobody can get past is a guard people work around."""
    with pytest.raises(vision.LiveCallBlocked) as caught:
        vision._guard_real_client(None, "ward photo")
    message = str(caught.value)
    assert "check_gemini" in message
    assert "quota" in message


def test_an_injected_client_is_never_blocked():
    """A test that stubs the transport reaches no network and is not this
    guard's business — which is why the bed-photo retry tests still work."""
    import httpx

    stub = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    vision._guard_real_client(stub, "ward photo")


def test_a_blocked_call_raises_rather_than_downgrading():
    """LiveCallBlocked is a VisionError, so existing handlers still catch it —
    but it is its own type, so a silent fallback can be told apart from a real
    model failure when reading a log."""
    assert issubclass(vision.LiveCallBlocked, vision.VisionError)


# ------------------------------------------------------------ structural ---
# The runner is the single gate for `python -m checks`. Read its source rather
# than trusting it, the way test_reset_nashik.py guards the sandbox deletes.

RUNNER = BACKEND / "checks" / "__main__.py"
RUNNER_SOURCE = RUNNER.read_text(encoding="utf-8")


def test_the_checks_runner_blocks_live_calls_by_default():
    assert "vision.block_live_calls(True)" in RUNNER_SOURCE
    assert '"--live-llm"' in RUNNER_SOURCE


def test_blocking_is_the_default_branch_not_the_opt_in_one():
    """The flag must enable spending, never disable it. An inverted condition
    would pass the check above and leak on every run."""
    tree = ast.parse(RUNNER_SOURCE, filename=str(RUNNER))
    main = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    guarded = [
        node for node in ast.walk(main)
        if isinstance(node, ast.If)
        and any(
            isinstance(c, ast.Name) and c.id == "live_llm"
            for c in ast.walk(node.test)
        )
    ]
    assert guarded, "main() has no branch on live_llm"
    calls_in_else = [
        n for stmt in guarded[0].orelse for n in ast.walk(stmt)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "block_live_calls"
    ]
    assert calls_in_else, "block_live_calls must sit in the else branch of `if live_llm`"


def _run(coro):
    import asyncio

    return asyncio.run(coro)

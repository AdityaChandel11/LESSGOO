"""Session-wide safety for the test suite.

There was no conftest here before, and the suite did not obviously need one:
every test is a pure-function test or an AST test over the source. But "does
not obviously need one" is exactly the state in which a future test quietly
starts making real network calls, and the thing it would call costs twenty
requests a day.

So this file exists to make one guarantee: **no test run can spend a real
Gemini request.** Three layers, because any one of them can be undone by
accident in a later edit:

  1. `llm_mode` is forced to "mock", so every mode-switched path takes the
     local branch;
  2. `gemini_api_key` is blanked, so even code that ignores the mode finds no
     credential;
  3. `vision.block_live_calls()` makes the attempt itself raise, loudly, at
     the one place that can open a connection to Google.

A test that injects its own httpx transport is untouched by all three — the
guard only refuses to build a *real* client — which is why the existing
bed-photo retry tests keep working unchanged.

Autouse and session-scoped on purpose. A fixture a test has to remember to
request is a fixture that protects only the tests that remembered.
"""

from __future__ import annotations

import pytest

from app import comms, vision
from app.config import settings


# Armed HERE, at import, not inside a fixture. A session-scoped autouse
# fixture does not run until the first test does, which leaves everything that
# executes at COLLECTION time unprotected: module-level statements in any
# test_*.py, and @pytest.mark.parametrize argument expressions. `pytest
# --collect-only` would never arm it at all. conftest.py is imported before any
# of that, so this is the earliest point that covers the whole run.
settings.llm_mode = "mock"
settings.gemini_api_key = ""
vision.block_live_calls(True)

# The same three layers for Twilio, added when the live send landed. A message
# costs real money and can reach a real handset, so a test that sends one is a
# worse accident than a test that spends a model request.
settings.comms_mode = "simulator"
settings.twilio_account_sid = ""
settings.twilio_auth_token = ""
settings.twilio_api_key_sid = ""
settings.twilio_api_key_secret = ""
comms.block_live_calls(True)


@pytest.fixture(autouse=True, scope="session")
def no_live_model_calls():
    """Hold the guard for the session and release it afterwards.

    The arming above is what protects collection; this exists so the process
    is left as it was found, which matters when pytest is embedded in a larger
    run rather than being the whole process.
    """
    try:
        yield
    finally:
        vision.block_live_calls(False)
        comms.block_live_calls(False)

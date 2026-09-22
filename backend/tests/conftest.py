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

from app import vision
from app.config import settings


@pytest.fixture(autouse=True, scope="session")
def no_live_model_calls():
    """Make a real Gemini call impossible for the whole test session."""
    previous_mode = settings.llm_mode
    previous_key = settings.gemini_api_key

    settings.llm_mode = "mock"
    settings.gemini_api_key = ""
    vision.block_live_calls(True)
    try:
        yield
    finally:
        vision.block_live_calls(False)
        settings.llm_mode = previous_mode
        settings.gemini_api_key = previous_key

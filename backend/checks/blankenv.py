"""Report how this app is configured when nothing is configured.

Run from a directory with no `.env` in it or above it, so pydantic-settings finds
nothing and Settings falls back to its own defaults. That is the only honest way
to check spec rule 3 — the app must start and demo with every credential blank —
because asserting on the *current* process would pass for the wrong reason as
soon as somebody turns a real key on.

    python -m checks.blankenv      # prints one JSON line
"""

from __future__ import annotations

import json


def main() -> None:
    from app.config import Settings

    # Built directly rather than via the cached singleton, so this reflects the
    # defaults in the code and nothing else.
    settings = Settings()
    payload = {
        "llm_mode": settings.llm_mode,
        "maps_mode": settings.maps_mode,
        "comms_mode": settings.comms_mode,
        "uses_external_services": settings.uses_external_services,
        "gemini_key_set": bool(settings.gemini_api_key),
        "maps_server_key_set": bool(settings.google_maps_server_key),
        "twilio_set": bool(settings.twilio_account_sid or settings.twilio_auth_token),
        "demo_mode": settings.demo_mode,
        "environment": settings.environment,
    }
    print(json.dumps(payload))


if __name__ == "__main__":
    main()

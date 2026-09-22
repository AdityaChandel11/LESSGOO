"""Gemini Vision — the single boundary to the model, spec 26.2.

One photo of a ward, one inference, two jobs: count the beds and read back the
day's rotating code that must be visible in the frame. The code is what makes
last week's photo fail, and reading it out of the same image is why this is
multimodal reasoning rather than an OCR pass bolted onto a counter.

LLM_MODE=mock   deterministic extraction, no key, no network. The whole
                pipeline — including a stale code and a photo taken somewhere
                else — is testable and demonstrable with a blank `.env`.
LLM_MODE=live   Gemini, asked for strict JSON.

A mock extraction is labelled `model="mock"` on the row it produces and is
never presented as real inference.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT_S = 30.0
MAX_IMAGE_BYTES = 6 * 1024 * 1024

# A shared flash model answers 503 when its capacity is tight, and 429 when the
# key is being rate-limited. Neither says anything about the photograph — the
# same image sent a second later is read fine — so a single attempt turns a
# working feature into one that fails in front of whoever is watching.
# Bounded deliberately: three tries inside the existing 30-second timeout, so a
# genuine outage still fails fast rather than hanging the request.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF_S = (0.6, 1.6)

PROMPT = (
    "This is a photograph of a ward in an Indian primary health centre. "
    "A short verification code is written on a whiteboard or slip somewhere in "
    "the frame.\n"
    "Report only what you can see:\n"
    "- beds_total: how many beds are visible in total\n"
    "- beds_occupied: how many of those are occupied (a person, or bedding "
    "clearly in use)\n"
    "- code_read: the verification code exactly as written, or null if no code "
    "is legible\n"
    "- confidence: 0.0 to 1.0, how sure you are of the bed counts\n"
    "- notes: anything that made counting hard, in one short sentence\n"
    "Do not guess the code. If it is blurred or absent, return null."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "beds_total": {"type": "integer"},
        "beds_occupied": {"type": "integer"},
        "code_read": {"type": "string", "nullable": True},
        "confidence": {"type": "number"},
        "notes": {"type": "string", "nullable": True},
    },
    "required": ["beds_total", "beds_occupied", "confidence"],
}


@dataclass(frozen=True)
class BedExtraction:
    beds_total: int | None
    beds_occupied: int | None
    code_read: str | None
    confidence: float
    notes: str | None
    model: str

    @property
    def is_mock(self) -> bool:
        return self.model == "mock"


class VisionError(Exception):
    """The photo could not be read. The caller stores the failure, never drops it."""


def parse_extraction(payload: dict, *, model: str) -> BedExtraction:
    """Normalise the model's JSON. Anything implausible becomes absent, not wrong."""
    def as_int(key: str) -> int | None:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value) if 0 <= value <= 500 else None

    total = as_int("beds_total")
    occupied = as_int("beds_occupied")
    # More beds occupied than exist is a misread, not a finding.
    if total is not None and occupied is not None and occupied > total:
        occupied = None

    code = payload.get("code_read")
    code = code.strip().upper() if isinstance(code, str) and code.strip() else None

    raw_conf = payload.get("confidence")
    confidence = (
        float(raw_conf) if isinstance(raw_conf, (int, float)) and 0 <= raw_conf <= 1 else 0.0
    )
    notes = payload.get("notes")
    return BedExtraction(
        beds_total=total,
        beds_occupied=occupied,
        code_read=code,
        confidence=confidence,
        notes=notes if isinstance(notes, str) and notes.strip() else None,
        model=model,
    )


def _mock_extraction(simulate: dict | None) -> BedExtraction:
    """What the demo submits instead of a photograph.

    `simulate` carries what the model would have seen, so a demo can show the
    stale-code and wrong-place paths deliberately. Accepted only in mock mode,
    and the row records `model="mock"`.
    """
    sim = simulate or {}
    return parse_extraction(
        {
            "beds_total": sim.get("beds_total", 20),
            "beds_occupied": sim.get("beds_occupied", 13),
            "code_read": sim.get("code_read"),
            "confidence": sim.get("confidence", 0.82),
            "notes": sim.get("notes", "Simulated extraction — no photograph was analysed."),
        },
        model="mock",
    )


# ============================================================ live guard ===
# A switch that makes an accidental paid call impossible rather than unlikely.
#
# The free tier allows twenty generate requests a day per model. An automated
# run that quietly spends one is worse than an outage: the suite still passes,
# nobody notices, and the quota is gone when a rehearsal needs it. This project
# has already had exactly that — an integration check that read a ward photo
# through the live model on every `python -m checks`, and a briefing check that
# did the same the day it was written.
#
# So the rule is enforced here, at the only place that can reach Google, rather
# than trusted to each caller:
#
#   * pytest turns it on for the whole session (tests/conftest.py);
#   * `python -m checks` turns it on unless --live-llm is passed;
#   * scripts/check_gemini.py deliberately does not, because a person running
#     it is choosing to spend the call.
#
# It blocks only calls that would build a *real* client. A test that injects an
# httpx mock transport passes `client=`, reaches no network, and is unaffected
# — which is why the existing bed-photo tests keep working unchanged.
#
# It raises rather than falling back on purpose. A silent downgrade to the mock
# path would hide the very mistake this exists to catch.

_block_live_calls = False


class LiveCallBlocked(VisionError):
    """A real model call was attempted while live calls were blocked."""


def block_live_calls(blocked: bool = True) -> None:
    """Turn the guard on or off. Called by test and check entry points."""
    global _block_live_calls
    _block_live_calls = blocked


def live_calls_blocked() -> bool:
    return _block_live_calls


def _is_stubbed(client: httpx.AsyncClient | None) -> bool:
    """Whether this client provably cannot reach the network.

    Only a mock or in-process ASGI transport qualifies. "Not None" is not the
    same question: a plain `httpx.AsyncClient()` carries the default transport
    and reaches Google exactly as if this module had built it. Threading a
    shared pooled client in for connection reuse is an ordinary refactor, and
    it must not silently disarm the guard.
    """
    if client is None:
        return False
    transport = getattr(client, "_transport", None)
    return isinstance(transport, (httpx.MockTransport, httpx.ASGITransport))


def _guard_real_client(client: httpx.AsyncClient | None, what: str) -> None:
    """Refuse a call that could reach the network while the guard is on."""
    if _block_live_calls and not _is_stubbed(client):
        raise LiveCallBlocked(
            "Refusing a live {0} call: this process blocked them "
            "(vision.block_live_calls). Automated runs must not spend the "
            "20-a-day quota. Inject an httpx client to stub it, or run "
            "`python -m scripts.check_gemini` if a real call is intended.".format(what)
        )


def _quota_note(response: httpx.Response) -> str:
    """Which limit was hit, for the log. Google names it; guessing wastes a day."""
    try:
        for detail in response.json().get("error", {}).get("details", []):
            for violation in detail.get("violations", []):
                if violation.get("quotaId"):
                    return " — {0} (limit {1})".format(
                        violation["quotaId"], violation.get("quotaValue", "?")
                    )
    except (ValueError, AttributeError, TypeError):
        pass
    return ""


def _first_json_object(text: str) -> dict:
    """Gemini returns JSON, occasionally wrapped in a code fence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionError("The model did not return readable JSON") from exc
    if not isinstance(parsed, dict):
        raise VisionError("The model returned something other than an object")
    return parsed


async def _call_gemini(
    image: bytes, mime_type: str, *, client: httpx.AsyncClient | None = None
) -> BedExtraction:
    if len(image) > MAX_IMAGE_BYTES:
        raise VisionError("Photo is too large; ask for a smaller one")
    model = settings.gemini_model
    body = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image).decode(),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            # A count, not prose: no room for the model to talk itself around.
            "temperature": 0.0,
        },
    }
    _guard_real_client(client, "ward photo")
    own = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    try:
        for attempt in range(len(RETRY_BACKOFF_S) + 1):
            try:
                response = await http.post(
                    f"{API_ROOT}/{model}:generateContent",
                    # The key travels in a header, never in the URL where it
                    # would be logged.
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in RETRY_STATUSES and attempt < len(RETRY_BACKOFF_S):
                    log.warning(
                        "Gemini answered HTTP %s; retrying in %.1fs",
                        status, RETRY_BACKOFF_S[attempt],
                    )
                    await asyncio.sleep(RETRY_BACKOFF_S[attempt])
                    continue
                log.error(
                    "Gemini refused the request (HTTP %s)%s",
                    status, _quota_note(exc.response),
                )
                # Three different things, and sending somebody to re-take a
                # photograph is only right for one of them. A spent quota does
                # not recover in a moment, and neither says anything about the
                # photo itself.
                if status == 429:
                    message = (
                        "The model's request quota for today is used up — the photo was not read"
                    )
                elif status in RETRY_STATUSES:
                    message = "The model is busy right now — send the photo again in a moment"
                else:
                    message = "The photo service rejected the request"
                raise VisionError(message) from exc
            except httpx.HTTPError as exc:
                log.warning("Gemini call failed: %s", type(exc).__name__)
                raise VisionError("The photo service could not be reached") from exc
    finally:
        if own:
            await http.aclose()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("The model returned no readable answer") from exc
    return parse_extraction(_first_json_object(text), model=model)


async def read_ward_photo(
    image: bytes | None,
    mime_type: str = "image/jpeg",
    *,
    simulate: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> BedExtraction:
    """Extract bed counts and the visible code from a ward photograph."""
    if settings.llm_mode != "live" or not settings.gemini_api_key:
        return _mock_extraction(simulate)
    if not image:
        raise VisionError("A photograph is required")
    return await _call_gemini(image, mime_type, client=client)


# =============================================================== briefing ===
# The second thing this file does, and the only other place a key is read.
#
# It runs on a *different* model from the ward photos on purpose. The free tier
# caps GenerateRequestsPerDayPerProjectPerModel at 20 — per model — so a day of
# bed photos cannot exhaust the briefings, or the reverse. `gemini_text_model`
# is a lite model because the job is one sentence in two languages.
#
# What this never does is invent a figure. Every number the model may use is
# handed to it in the prompt, and the prompt says so; the deterministic line in
# `workspace.rules_briefing` is what renders when this is unavailable, out of
# quota, or switched off, and it renders without an AI label.

BRIEFING_PROMPT = (
    "You write one short line of guidance for a pharmacist at a rural Indian "
    "primary health centre, from their own stock position.\n"
    "Rules:\n"
    "- Use ONLY the figures given below. Never invent a number, a date or a "
    "medicine name.\n"
    "- One sentence in English, one in Hindi. Plain words a busy person can "
    "act on.\n"
    "- Say what to do first, not what the data says.\n"
    "- No clinical, dosing or treatment advice. This is about stock.\n"
    "Centre: {facility}\n"
    "Stock position today:\n"
    "{rows}\n"
)

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {"en": {"type": "string"}, "hi": {"type": "string"}},
    "required": ["en", "hi"],
}

# One sentence each. Generous enough for Devanagari, which costs more tokens
# per character than Latin script.
BRIEFING_MAX_TOKENS = 400


@dataclass(frozen=True)
class Briefing:
    en: str
    hi: str
    model: str


def parse_briefing(payload: dict, *, model: str) -> Briefing:
    """Validate what came back before any of it reaches a screen."""
    en = (payload.get("en") or "").strip()
    hi = (payload.get("hi") or "").strip()
    if not en or not hi:
        raise VisionError("The model did not return both languages")
    return Briefing(en=en, hi=hi, model=model)


def briefing_available() -> bool:
    """Whether the live briefing path can run at all.

    Exists so that callers can ask without reading the credential themselves.
    `gemini_api_key` belongs to this module and nowhere else (pinned by
    tests/test_api_boundary.py), and an endpoint that checked the key inline
    would quietly make api.py a second owner of it.
    """
    return settings.llm_mode == "live" and bool(settings.gemini_api_key)


async def write_briefing(
    *,
    facility_name: str,
    rows: list[str],
    client: httpx.AsyncClient | None = None,
) -> Briefing:
    """One line of guidance, in English and Hindi, from a live model.

    `rows` are pre-formatted lines built by the caller from figures already on
    screen — this function never reads the database, so there is no path by
    which it can describe something the pharmacist is not also looking at.

    Raises VisionError for every failure, including a spent quota. The caller
    is expected to fall back to the deterministic line rather than retry.
    """
    if settings.llm_mode != "live" or not settings.gemini_api_key:
        raise VisionError("The briefing model is not configured")
    if not rows:
        raise VisionError("There is no stock position to describe")

    model = settings.gemini_text_model
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": BRIEFING_PROMPT.format(
                            facility=facility_name, rows="\n".join(rows)
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": BRIEFING_SCHEMA,
            # Low, not zero: one sentence of advice reads better with a little
            # freedom than a temperature-zero template, and the figures it may
            # use are fixed by the prompt either way.
            "temperature": 0.2,
            "maxOutputTokens": BRIEFING_MAX_TOKENS,
        },
    }

    _guard_real_client(client, "briefing")
    own = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    try:
        for attempt in range(len(RETRY_BACKOFF_S) + 1):
            try:
                response = await http.post(
                    f"{API_ROOT}/{model}:generateContent",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in RETRY_STATUSES and attempt < len(RETRY_BACKOFF_S):
                    log.warning(
                        "Briefing model answered HTTP %s; retrying in %.1fs",
                        status, RETRY_BACKOFF_S[attempt],
                    )
                    await asyncio.sleep(RETRY_BACKOFF_S[attempt])
                    continue
                log.error(
                    "Briefing model refused the request (HTTP %s)%s",
                    status, _quota_note(exc.response),
                )
                # Deliberately not distinguished for the caller: every one of
                # these ends in the same place, which is the deterministic line.
                raise VisionError(
                    "today's quota is used up"
                    if status == 429
                    else f"the briefing model refused the request (HTTP {status})"
                ) from exc
            except httpx.HTTPError as exc:
                log.warning("Briefing call failed: %s", type(exc).__name__)
                raise VisionError("the briefing model could not be reached") from exc
    finally:
        if own:
            await http.aclose()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("the model returned no readable answer") from exc
    return parse_briefing(_first_json_object(text), model=model)

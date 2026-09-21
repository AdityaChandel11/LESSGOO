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

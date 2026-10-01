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
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date

import httpx

from . import stockphoto
from .config import settings

log = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
# Text and images are not the same wait. A one-sentence briefing comes back in
# about two seconds; a photograph of a delivery note measured 10.1s on a warm
# local connection on 2026-09-23, and a free-tier container that has just woken
# up is slower than that. One flat 30s timeout meant every bill photo failed
# with "the model could not be reached" while the briefing beside it worked,
# which reads as a broken feature rather than a tight deadline. Connect stays
# short either way, so a genuine outage still fails fast instead of hanging.
REQUEST_TIMEOUT_S = 30.0
VISION_TIMEOUT_S = 60.0
CONNECT_TIMEOUT_S = 10.0
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


async def _post_generate(
    model: str,
    body: dict,
    *,
    client: httpx.AsyncClient | None,
    what: str,
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> dict:
    """One generateContent call, with the guard, the retries and the honest
    error messages that every caller in this module needs.

    Extracted because there were two copies of this loop and a third was about
    to be written. Three copies of a retry policy is three places for a quota
    message to drift out of step with what actually happened.

    `what` names the caller in the guard's refusal, so a blocked call says
    which feature tried to make it.
    """
    _guard_real_client(client, what)
    own = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S)
    )
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
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in RETRY_STATUSES and attempt < len(RETRY_BACKOFF_S):
                    log.warning(
                        "Gemini answered HTTP %s for %s; retrying in %.1fs",
                        status, what, RETRY_BACKOFF_S[attempt],
                    )
                    await asyncio.sleep(RETRY_BACKOFF_S[attempt])
                    continue
                log.error(
                    "Gemini refused the %s request (HTTP %s)%s",
                    what, status, _quota_note(exc.response),
                )
                # Three different things, and sending somebody to re-take a
                # photograph is only right for one of them. A spent quota does
                # not recover in a moment, and neither says anything about the
                # photo itself.
                if status == 429:
                    message = (
                        "the model's request quota for today is used up — "
                        "nothing was read"
                    )
                elif status in RETRY_STATUSES:
                    message = "the model is busy right now — try again in a moment"
                else:
                    message = "the model rejected the request"
                raise VisionError(message) from exc
            except httpx.HTTPError as exc:
                log.warning("Gemini call failed for %s: %s", what, type(exc).__name__)
                raise VisionError("the model could not be reached") from exc
    finally:
        if own:
            await http.aclose()
    raise VisionError("the model could not be reached")


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
    data = await _post_generate(
        model, body, client=client, what="ward photo", timeout_s=VISION_TIMEOUT_S
    )
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
# is a lite model because the job is a short list in two or three languages.
#
# What this never does is invent a figure. Every number the model may use is
# handed to it in the prompt, the prompt says so, and the answer is checked
# against those figures; the computed list in `workspace.todo_items` is what renders when this is unavailable, out of
# quota, or switched off, and it renders without an AI label.

BRIEFING_PROMPT = (
    "You write today's to-do list for a pharmacist at a rural Indian primary "
    "health centre. The items below were computed from the centre's own "
    "records and are already in priority order.\n"
    "Rules:\n"
    "- Use ONLY the facts and figures given below. Never invent a number, a "
    "date, a medicine name or a place. Do not calculate any new figure. Write "
    "every number in Western digits (0-9).\n"
    "- Keep the order. One short line per item, in plain words a busy person "
    "can act on: what to do first, then why.\n"
    "- You may join two items about the same medicine into one line. Never "
    "add an item that is not below.\n"
    "- No clinical, dosing or treatment advice. This is about stock, "
    "deliveries, beds and attendance records.\n"
    "- Write the whole list in {languages}. Keep medicine and centre names as "
    "given.\n"
    "Centre: {facility}\n"
    "Today's items:\n"
    "{rows}\n"
)

_LINES = {"type": "array", "items": {"type": "string"}}


def briefing_schema(local: bool) -> dict:
    """`local` is the state's language, asked for only where one is named."""
    properties = {"en": _LINES, "hi": _LINES}
    if local:
        properties["local"] = _LINES
    return {"type": "object", "properties": properties, "required": list(properties)}


# A short line per item in up to three scripts. Generous enough for
# Devanagari and the southern scripts, which cost more tokens per character
# than Latin.
BRIEFING_MAX_TOKENS = 1600


@dataclass(frozen=True)
class Briefing:
    # Language code -> the list in that language, in the order it was given.
    lines: dict[str, list[str]]
    model: str


def parse_briefing(
    payload: dict, *, model: str, sources: list[str], local: str | None
) -> Briefing:
    """Validate what came back before any of it reaches a screen.

    `sources` are the computed lines the model was given. An answer with more
    lines than that has added something, and an answer holding a figure that
    is in none of them has invented one; either is discarded whole, and the
    caller shows the computed list instead.
    """
    wanted = {"en": "en", "hi": "hi"}
    if local:
        wanted[local] = "local"
    lines: dict[str, list[str]] = {}
    for code, key in wanted.items():
        raw = payload.get(key)
        got = [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []
        if not got:
            raise VisionError("The model did not return every language it was asked for")
        if len(got) > len(sources):
            raise VisionError("The model returned more lines than there are items")
        if ungrounded_figures(" ".join(got), sources):
            raise VisionError("The model used a figure that is not in this centre's records")
        lines[code] = got
    return Briefing(lines=lines, model=model)


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
    local: tuple[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Briefing:
    """Today's list in English, Hindi and the state's language, from a live model.

    `rows` are the computed to-do lines (workspace.todo_items) — this function
    never reads the database, so there is no path by which it can describe
    something the pharmacist is not also looking at. `local` is the state
    language as (code, English name), or None where the state's language is
    Hindi.

    Raises VisionError for every failure, including a spent quota. The caller
    is expected to fall back to the computed list rather than retry.
    """
    if settings.llm_mode != "live" or not settings.gemini_api_key:
        raise VisionError("The briefing model is not configured")
    if not rows:
        raise VisionError("There is nothing to describe")

    model = settings.gemini_text_model
    languages = "English, in Hindi and in {0}".format(local[1]) if local else "English and in Hindi"
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": BRIEFING_PROMPT.format(
                            facility=facility_name,
                            languages=languages,
                            rows="\n".join("- {0}".format(r) for r in rows),
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": briefing_schema(bool(local)),
            # Low, not zero: a line of advice reads better with a little
            # freedom than a temperature-zero template, and the figures it may
            # use are fixed by the prompt and checked afterwards either way.
            "temperature": 0.2,
            "maxOutputTokens": BRIEFING_MAX_TOKENS,
        },
    }

    _guard_real_client(client, "briefing")
    data = await _post_generate(model, body, client=client, what="briefing")
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("the model returned no readable answer") from exc
    return parse_briefing(
        _first_json_object(text), model=model, sources=rows, local=local[0] if local else None
    )


# ======================================================= plain-language why ===
# Two places where a screen already shows the figures but a person still has to
# work out what they add up to: a proposed transfer an officer must approve, and
# a facility whose records disagree with each other. The model writes that
# sentence from the same figures the screen shows and nothing else.
#
# The decision stays with the rules either way. The solver chose the transfer
# and the trust rules scored the facility (spec 1.7); this only words them.
#
# Clicked, never loaded: the free tier is 20 requests a day per model, so each
# answer is cached by its exact inputs and the same question is never paid for
# twice while the process lives.

EXPLAIN_MAX_TOKENS = 300
EXPLAIN_CACHE_SIZE = 512

TRANSFER_PROMPT = (
    "You explain one proposed medicine transfer between two public health "
    "facilities in India to the district officer who must approve or reject it.\n"
    "Rules:\n"
    "- Use ONLY the figures given below. Never invent a number, a place or a "
    "medicine. Do not calculate percentages or any new figure.\n"
    "- One or two plain sentences, under 50 words in total.\n"
    "- Say why the receiver needs it and why the donor can spare it.\n"
    "- Do not tell the officer what to decide. No clinical or dosing advice.\n"
    "Transfer:\n{rows}\n"
)

TRUST_PROMPT = (
    "You explain to a district health officer why one health facility's own "
    "records disagree with each other, so they can judge whether a visit is "
    "worth making.\n"
    "Rules:\n"
    "- Use ONLY the findings given below. Never invent a number. Do not "
    "calculate percentages or any new figure.\n"
    "- Two plain sentences, under 60 words in total: how the findings relate "
    "to each other, then the most likely ordinary explanation, such as "
    "registers not being filled in.\n"
    "- Talk about the facility and its records only. Never mention or guess "
    "at any person.\n"
    "- Never accuse anyone. Never use the words fraud, theft, corruption, "
    "fake or cheating.\n"
    "Facility: {facility}\n"
    "Data confidence: {score} out of 100\n"
    "Findings:\n{rows}\n"
)

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}

# Spec 12.6: the trust layer is never described as fraud detection and never
# points at a person. A sentence that reads as an accusation is not shown.
ACCUSATORY = re.compile(
    r"\b(fraud\w*|theft|thie(f|ves)|steal\w*|stole\w*|corrupt\w*|fake\w*|"
    r"cheat\w*|embezzl\w*|scam\w*|misconduct|dishonest\w*|lying|liars?)\b",
    re.IGNORECASE,
)

_NUMBER = re.compile(r"\d+(?:,\d{2,3})*(?:\.\d+)?")


@dataclass(frozen=True)
class Explanation:
    text: str
    model: str
    latency_ms: int
    cached: bool = False


def _figure(value: float) -> str:
    return "{0:g}".format(value)


def ungrounded_figures(text: str, sources: list[str]) -> list[str]:
    """Numbers in `text` that appear nowhere in `sources`, allowing rounding.

    "0.62 days" in the inputs may come back as "0.6" or "1"; a number the
    inputs never contained, or a percentage worked out from them, may not.
    """
    allowed: set[str] = set()
    for raw in _NUMBER.findall(" ".join(sources)):
        value = float(raw.replace(",", ""))
        allowed |= {_figure(value), _figure(round(value)), _figure(round(value, 1))}
    return [
        raw for raw in _NUMBER.findall(text)
        if _figure(float(raw.replace(",", ""))) not in allowed
    ]


def parse_explanation(
    payload: dict,
    *,
    model: str,
    sources: list[str],
    latency_ms: int,
    forbid_accusation: bool = False,
) -> Explanation:
    """Validate what came back before any of it reaches a screen."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise VisionError("the model returned an empty explanation")
    if forbid_accusation and ACCUSATORY.search(text):
        raise VisionError("the model's wording read as an accusation, so it was not shown")
    if ungrounded_figures(text, sources):
        raise VisionError("the model used a figure that is not in the data, so it was not shown")
    return Explanation(text=text, model=model, latency_ms=latency_ms)


_explain_cache: "OrderedDict[str, Explanation]" = OrderedDict()


def clear_explanation_cache() -> None:
    _explain_cache.clear()


def explanation_available() -> bool:
    return settings.llm_mode == "live" and bool(settings.gemini_api_key)


async def _explain(
    prompt: str,
    *,
    sources: list[str],
    what: str,
    forbid_accusation: bool,
    client: httpx.AsyncClient | None,
) -> Explanation:
    if not explanation_available():
        raise VisionError("the explanation model is not configured")
    model = settings.gemini_text_model
    key = hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()
    hit = _explain_cache.get(key)
    if hit is not None:
        _explain_cache.move_to_end(key)
        return replace(hit, cached=True)

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": EXPLAIN_SCHEMA,
            "temperature": 0.2,
            "maxOutputTokens": EXPLAIN_MAX_TOKENS,
        },
    }
    started = time.perf_counter()
    data = await _post_generate(model, body, client=client, what=what)
    latency_ms = round((time.perf_counter() - started) * 1000)
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("the model returned no readable answer") from exc
    answer = parse_explanation(
        _first_json_object(text),
        model=model,
        sources=sources,
        latency_ms=latency_ms,
        forbid_accusation=forbid_accusation,
    )
    _explain_cache[key] = answer
    while len(_explain_cache) > EXPLAIN_CACHE_SIZE:
        _explain_cache.popitem(last=False)
    return answer


async def explain_transfer(
    *, rows: list[str], client: httpx.AsyncClient | None = None
) -> Explanation:
    """Why this trip, in one or two sentences, from the solver's own figures."""
    if not rows:
        raise VisionError("there is no transfer to explain")
    return await _explain(
        TRANSFER_PROMPT.format(rows="\n".join(rows)),
        sources=rows,
        what="transfer explanation",
        forbid_accusation=False,
        client=client,
    )


async def explain_trust(
    *,
    facility_name: str,
    score: int,
    rows: list[str],
    client: httpx.AsyncClient | None = None,
) -> Explanation:
    """How a facility's disagreeing signals relate, worded as a reason to look."""
    if not rows:
        raise VisionError("there is no disagreement to explain")
    return await _explain(
        TRUST_PROMPT.format(facility=facility_name, score=score, rows="\n".join(rows)),
        sources=[facility_name, f"{score} out of 100", *rows],
        what="trust explanation",
        forbid_accusation=True,
        client=client,
    )


# ========================================================== stock photo ===
# The consumption side of spec 26.3, and the second job this file does with a
# camera. A ward photo answers "how many beds are occupied"; a bill or delivery
# slip answers "what arrived, and how much of it".
#
# Same model as the ward photo, deliberately: it is the same kind of question
# — read what is visibly written and report only that — and putting it on the
# same model keeps one quota to reason about rather than two.
#
# What it must never do is guess. A bill that is creased, dark or half out of
# frame produces a low confidence and a short line list, and the endpoint above
# it refuses to commit anything it could not resolve to a known medicine. An
# invented quantity on a stock ledger is worse than no reading at all: the
# reading is what the reorder threshold, the forecast and the redistribution
# solver all read next.

# The same number means three different things on three different documents,
# so the model is asked which document it is looking at before any number is
# used (fix list #11; app/stockphoto.py decides what each kind does).
STOCK_PROMPT = (
    "This is a photograph of a stock document from an Indian primary health "
    "centre.\n"
    "First decide which kind of document it is, and report it as document_type:\n"
    "- delivery_slip: a delivery note, challan, dispatch slip or supplier's invoice "
    "listing stock SENT TO this centre\n"
    "- issue_record: a dispensing bill, issue register or issue voucher listing "
    "stock GIVEN OUT or used by this centre\n"
    "- stock_count: a stock register page or count sheet stating what is ON THE "
    "SHELF (closing balance)\n"
    "- unknown: anything else, or if you cannot tell\n"
    "Then report only what is legibly written:\n"
    "- lines: one entry per medicine, each with `medicine` exactly as printed, "
    "`quantity` as a number, `unit` exactly as printed (null if no unit is "
    "written) and `batch` as printed (null if none)\n"
    "- document_date: the date printed on the document in YYYY-MM-DD form, or "
    "null if none is legible\n"
    "- confidence: 0.0 to 1.0, how sure you are of the lines as a whole\n"
    "- notes: anything that made it hard to read, in one short sentence\n"
    "Do not infer a medicine that is not written. Do not convert units. Do not "
    "total anything. If a quantity is unreadable, omit that line entirely."
)

# document_type is a plain string rather than a schema enum: the parser below
# maps anything outside the three kinds to "unknown", which is the same
# guarantee without depending on how the API validates enums.
STOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "medicine": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string", "nullable": True},
                    "batch": {"type": "string", "nullable": True},
                },
                "required": ["medicine", "quantity"],
            },
        },
        "document_date": {"type": "string", "nullable": True},
        "confidence": {"type": "number"},
        "notes": {"type": "string", "nullable": True},
    },
    "required": ["document_type", "lines", "confidence"],
}


@dataclass(frozen=True)
class StockLine:
    medicine: str
    quantity: float
    # As printed, or None when the document does not say.
    unit: str | None = None
    batch: str | None = None


@dataclass(frozen=True)
class StockExtraction:
    lines: list[StockLine]
    document_date: date | None
    confidence: float
    notes: str | None
    model: str
    # One of stockphoto.DOCUMENT_TYPES, or "unknown".
    document_type: str = stockphoto.UNKNOWN


def parse_stock_extraction(payload: dict, *, model: str) -> StockExtraction:
    """Validate the model's answer before any of it reaches a ledger.

    Everything questionable is dropped rather than repaired. A line with no
    medicine name, a quantity that is not a number, or a negative quantity is
    not a line — and a document date that does not parse is simply absent,
    because a wrong date on a stock reading silently corrupts the burn rate
    that every forecast is computed from.
    """
    lines: list[StockLine] = []
    for raw in payload.get("lines") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("medicine") or "").strip()
        try:
            qty = float(raw.get("quantity"))
        except (TypeError, ValueError):
            continue
        if not name or qty < 0:
            continue
        lines.append(
            StockLine(
                medicine=name,
                quantity=qty,
                unit=_printed(raw.get("unit")),
                batch=_printed(raw.get("batch")),
            )
        )

    kind = payload.get("document_type")
    kind = kind.strip().lower() if isinstance(kind, str) else ""

    parsed_date: date | None = None
    raw_date = payload.get("document_date")
    if isinstance(raw_date, str) and raw_date.strip():
        try:
            parsed_date = date.fromisoformat(raw_date.strip()[:10])
        except ValueError:
            parsed_date = None

    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)

    notes = payload.get("notes")
    return StockExtraction(
        lines=lines,
        document_date=parsed_date,
        confidence=confidence,
        notes=str(notes).strip() if notes else None,
        model=model,
        document_type=kind if kind in stockphoto.DOCUMENT_TYPES else stockphoto.UNKNOWN,
    )


def _printed(raw) -> str | None:
    """A unit or batch exactly as the document prints it, or None."""
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def _mock_stock_extraction() -> StockExtraction:
    """What the mock path returns. Labelled `model="mock"` on every row it
    produces, and never presented as real inference. A stock count, because
    it is the one kind of document that needs nothing else in the database to
    be applied."""
    return StockExtraction(
        lines=[StockLine(medicine="ORS", quantity=250.0, unit="sachets")],
        document_date=None,
        confidence=settings.channel_confidence_floor,
        notes="mock extraction — no model was called",
        model="mock",
        document_type=stockphoto.STOCK_COUNT,
    )


# ============================================ IDSP weekly report (#42) ===
# Gemini reads the outbreak rows out of the IDSP Weekly Outbreak Report PDF.
# Every row carries the text it was read from, so the regex parser can re-read
# it and flag disagreement (idsp.cross_check). No mock: without the live model
# a report cannot be read, and the panel says so.

MAX_REPORT_BYTES = 12 * 1024 * 1024
REPORT_TIMEOUT_S = 120.0

IDSP_PROMPT = (
    "This PDF is an IDSP Weekly Outbreak Report published by NCDC, India. It lists "
    "disease outbreaks reported by states in one week, one row per outbreak, each "
    "starting with a unique ID such as MH/NSK/2026/38/1021.\n"
    "Return the report's year and week, and every outbreak row with:\n"
    "- unique_id: exactly as printed\n"
    "- state, district, disease: exactly as printed\n"
    "- cases, deaths: the numbers printed in those columns\n"
    "- start_date, reported_date: as printed, DD-MM-YYYY; empty if the column is absent\n"
    "- status: the Current Status column as printed; empty if absent\n"
    "- row_text: the row's printed text from the unique ID to the status, verbatim, "
    "leaving out the Comments/Action Taken column\n"
    "Copy numbers exactly. Never estimate, total or infer a value; if a row cannot "
    "be read, leave it out."
)

IDSP_SCHEMA = {
    "type": "object",
    "properties": {
        "year": {"type": "integer"},
        "week": {"type": "integer"},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unique_id": {"type": "string"},
                    "state": {"type": "string"},
                    "district": {"type": "string"},
                    "disease": {"type": "string"},
                    "cases": {"type": "integer"},
                    "deaths": {"type": "integer"},
                    "start_date": {"type": "string"},
                    "reported_date": {"type": "string"},
                    "status": {"type": "string"},
                    "row_text": {"type": "string"},
                },
                "required": ["unique_id", "state", "district", "disease", "cases", "deaths", "row_text"],
            },
        },
    },
    "required": ["rows"],
}


@dataclass
class IdspRead:
    year: int | None
    week: int | None
    rows: list[dict]
    # Rows the model returned that were dropped as unreadable.
    dropped: int
    model: str


async def read_idsp_report(
    pdf: bytes, *, client: httpx.AsyncClient | None = None
) -> IdspRead:
    """Every outbreak row in one IDSP weekly report, read by the model."""
    if settings.llm_mode != "live" or not settings.gemini_api_key:
        raise VisionError(
            "reading an IDSP report needs the live model (LLM_MODE=live); this deployment "
            "runs without it"
        )
    if not pdf:
        raise VisionError("A PDF is required")
    if len(pdf) > MAX_REPORT_BYTES:
        raise VisionError("The report is too large to read in one request")

    from . import idsp

    model = settings.gemini_model
    body = {
        "contents": [
            {
                "parts": [
                    {"text": IDSP_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "application/pdf",
                            "data": base64.b64encode(pdf).decode(),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": IDSP_SCHEMA,
            "temperature": 0.0,
        },
    }
    data = await _post_generate(
        model, body, client=client, what="IDSP report", timeout_s=REPORT_TIMEOUT_S
    )
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("The model returned no readable answer") from exc
    parsed = _first_json_object(text)
    raw_rows = parsed.get("rows") if isinstance(parsed.get("rows"), list) else []
    rows = [r for r in (idsp.normalise_row(x) for x in raw_rows if isinstance(x, dict)) if r]
    year = parsed.get("year") if isinstance(parsed.get("year"), int) else None
    week = parsed.get("week") if isinstance(parsed.get("week"), int) else None
    if rows and (year is None or week is None):
        year, week = rows[0]["year"], rows[0]["week"]
    return IdspRead(year=year, week=week, rows=rows, dropped=len(raw_rows) - len(rows), model=model)


async def read_stock_photo(
    image: bytes | None,
    mime_type: str = "image/jpeg",
    *,
    client: httpx.AsyncClient | None = None,
) -> StockExtraction:
    """Read a medicine bill or delivery slip into structured lines."""
    if settings.llm_mode != "live" or not settings.gemini_api_key:
        return _mock_stock_extraction()
    if not image:
        raise VisionError("A photograph is required")
    if len(image) > MAX_IMAGE_BYTES:
        raise VisionError("Photo is too large; ask for a smaller one")

    model = settings.gemini_model
    body = {
        "contents": [
            {
                "parts": [
                    {"text": STOCK_PROMPT},
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
            "responseSchema": STOCK_SCHEMA,
            # Quantities, not prose. No room for the model to round or reason.
            "temperature": 0.0,
        },
    }
    data = await _post_generate(
        model, body, client=client, what="stock photo", timeout_s=VISION_TIMEOUT_S
    )
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("The model returned no readable answer") from exc
    return parse_stock_extraction(_first_json_object(text), model=model)

"""Gemini reads the IDSP report (fix list #42).

An officer gives the platform this week's IDSP Weekly Outbreak Report PDF;
Gemini reads every outbreak row out of it; the regex parser the project
already had re-reads each row from the printed text Gemini transcribed, and
any field the two disagree on is flagged. Rows both agree on, in districts the
network has centres in, become active outbreaks for #41. Remove Gemini and a
new report no longer turns into warnings: that is the load it carries.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import idsp, outbreak, vision
from app.config import settings

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

ROW_TEXT = (
    "MH/NSK/2026/38/1021 Maharashtra Nashik Cholera 23 1 14-09-2026 18-09-2026 Under Surveillance"
)
GEMINI_ROW = {
    "unique_id": "MH/NSK/2026/38/1021",
    "state": "Maharashtra",
    "district": "Nashik",
    "disease": "Cholera",
    "cases": 23,
    "deaths": 1,
    "start_date": "14-09-2026",
    "reported_date": "18-09-2026",
    "status": "Under Surveillance",
    "row_text": ROW_TEXT,
}


def _live(monkeypatch) -> None:
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    monkeypatch.setattr(vision, "RETRY_BACKOFF_S", (0.0, 0.0))


def _answer(payload: dict) -> httpx.Response:
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    )


# ---------------------------------------------------------------- reading


def test_the_model_reads_rows_out_of_the_pdf(monkeypatch):
    _live(monkeypatch)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return _answer({"year": 2026, "week": 38, "rows": [GEMINI_ROW]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    read = asyncio.run(vision.read_idsp_report(b"%PDF-1.7 bytes", client=client))
    part = seen[0]["contents"][0]["parts"][1]["inline_data"]
    assert part["mime_type"] == "application/pdf"
    assert (read.year, read.week, read.model) == (2026, 38, settings.gemini_model)
    (row,) = read.rows
    assert row["unique_id"] == "MH/NSK/2026/38/1021"
    assert row["state_code"] == "MH"
    assert row["start_date"] == "2026-09-14" and row["reported_date"] == "2026-09-18"
    assert (row["cases"], row["deaths"]) == (23, 1)


def test_without_the_live_model_a_report_cannot_be_read(monkeypatch):
    monkeypatch.setattr(vision.settings, "llm_mode", "mock")
    with pytest.raises(vision.VisionError) as exc:
        asyncio.run(vision.read_idsp_report(b"%PDF-1.7 bytes"))
    assert "live model" in str(exc.value)


def test_an_oversized_report_is_refused_before_any_call(monkeypatch):
    _live(monkeypatch)
    big = b"%PDF" + b"0" * (vision.MAX_REPORT_BYTES + 1)
    with pytest.raises(vision.VisionError):
        asyncio.run(vision.read_idsp_report(big))


def test_rows_with_implausible_fields_are_dropped_not_guessed():
    bad = {**GEMINI_ROW, "cases": "many"}
    assert idsp.normalise_row(bad) is None
    assert idsp.normalise_row({**GEMINI_ROW, "unique_id": "not an id"}) is None


# ------------------------------------------------------------ cross-check


def test_the_parser_agrees_when_the_fields_match_the_printed_row():
    row = idsp.normalise_row(GEMINI_ROW)
    assert idsp.cross_check(row) == {"verdict": "agrees", "fields": []}


def test_a_field_the_model_got_wrong_is_flagged():
    row = idsp.normalise_row({**GEMINI_ROW, "cases": 32})
    assert idsp.cross_check(row) == {"verdict": "disagrees", "fields": ["cases"]}


def test_a_row_the_parser_cannot_read_is_marked_not_trusted_blindly():
    row = idsp.normalise_row({**GEMINI_ROW, "row_text": "garbled"})
    assert idsp.cross_check(row)["verdict"] == "unparsed"


# -------------------------------------------------------------- activation


def test_a_row_is_active_for_the_spec_ttl_from_its_report_date():
    row = idsp.normalise_row(GEMINI_ROW)
    expires = outbreak.idsp_expiry(row, NOW)
    assert expires == datetime(2026, 9, 18, tzinfo=timezone.utc) + timedelta(
        days=settings.outbreak_ttl_days
    )


def test_a_row_from_an_old_report_is_history_not_an_active_outbreak():
    row = idsp.normalise_row({**GEMINI_ROW, "reported_date": "18-06-2023", "start_date": "10-06-2023"})
    assert outbreak.idsp_expiry(row, NOW) is None


def test_only_rows_the_parser_agrees_with_can_activate():
    agreed = {**idsp.normalise_row(GEMINI_ROW), "check": {"verdict": "agrees", "fields": []}}
    flagged = {**agreed, "check": {"verdict": "disagrees", "fields": ["cases"]}}
    assert outbreak.may_activate(agreed)
    assert not outbreak.may_activate(flagged)
    assert not outbreak.may_activate({**agreed, "state_code": None})


def test_an_idsp_district_matches_the_networks_spelling_whatever_the_case():
    names = ["Nashik", "Pune", "Mumbai Suburban"]
    assert outbreak.match_district(names, "NASHIK") == "Nashik"
    assert outbreak.match_district(names, " mumbai  suburban ") == "Mumbai Suburban"
    assert outbreak.match_district(names, "Nasik") is None


# ------------------------------------------------------------------ the API

from fastapi import HTTPException  # noqa: E402

from app import api  # noqa: E402
from app.auth import Principal  # noqa: E402

REAL_ADMIN = Principal(3, "admin@health.example", "Admin", "admin", None, None, None, None)
DEMO_ADMIN = Principal(1, "admin@demo.swasthsetu.in", "Platform Admin", "admin", None, None, None, None)


class CachedSession:
    def __init__(self, cached):
        self.cached = cached

    async def scalar(self, *_a, **_k):
        return self.cached


def _upload(user, session):
    import base64

    body = api.IdspReportIn(pdf_base64=base64.b64encode(b"%PDF-1.7 x").decode(), filename="w38.pdf")
    return asyncio.run(api.read_idsp_report(body, session=session, user=user))


def test_a_public_demo_account_cannot_spend_the_model_on_an_upload(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as exc:
        _upload(DEMO_ADMIN, CachedSession(None))
    assert exc.value.status_code == 403


def test_a_report_read_before_is_not_read_again(monkeypatch):
    from app.models import IdspReport

    async def never(*_a, **_k):
        raise AssertionError("the model was called for a report it has already read")

    monkeypatch.setattr(vision, "read_idsp_report", never)
    row = {**idsp.normalise_row(GEMINI_ROW), "check": {"verdict": "agrees", "fields": []},
           "in_network": True, "activated": True}
    cached = IdspReport(id=1, sha256="x", year=2026, week=38, source="w38.pdf",
                        model="gemini-test", read_by="Admin", rows=[row], dropped=0,
                        read_at=NOW)
    out = _upload(REAL_ADMIN, CachedSession(cached))
    assert out.cached is True and out.week == 38 and out.agrees == 1


def test_without_the_live_model_the_upload_says_so(monkeypatch):
    monkeypatch.setattr(vision.settings, "llm_mode", "mock")
    with pytest.raises(HTTPException) as exc:
        _upload(REAL_ADMIN, CachedSession(None))
    assert exc.value.status_code == 503
    assert "live model" in exc.value.detail


def test_old_reports_are_pruned_to_the_last_twelve():
    assert settings.idsp_reports_kept == 12

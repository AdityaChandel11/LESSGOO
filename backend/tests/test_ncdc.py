"""Live IDSP intake (fix list #57). Aditya's decision: check-on-use plus a
button, never a scheduler. NCDC's listing is read, the newest weekly report
found, and only a report newer than the last one read is fetched and sent to
the model (#42) — at most once per new week, whoever asks."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import ncdc

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

LISTING = """
<a href="../uploads/weekly_outbreaks/2025/week52_1767000000.pdf">52</a>
<a href="../uploads/weekly_outbreaks/2026/week9_1788856576.pdf">9</a>
<a href="../uploads/weekly_outbreaks/2026/week32_1790680151.pdf">32</a>
<a href="../uploads/weekly_outbreaks/2026/week31_1790077762.pdf">31</a>
<a href="../uploads/weekly_outbreaks/2009/week25.pdf">old</a>
"""


def test_the_newest_week_is_found_in_the_listing():
    r = ncdc.latest_report(LISTING)
    assert (r.year, r.week) == (2026, 32)
    assert r.url == "https://ncdc.mohfw.gov.in/uploads/weekly_outbreaks/2026/week32_1790680151.pdf"
    assert r.uploaded_on == date(2026, 9, 29)


def test_a_listing_without_reports_gives_nothing():
    assert ncdc.latest_report("<html>maintenance</html>") is None


def test_only_a_newer_week_is_fetched():
    assert ncdc.is_newer((2026, 33), (2026, 32))
    assert ncdc.is_newer((2027, 1), (2026, 52))
    assert not ncdc.is_newer((2026, 32), (2026, 32))
    assert ncdc.is_newer((2026, 32), None)


def test_check_on_use_runs_at_most_once_a_day():
    assert ncdc.check_due(None, NOW)
    assert not ncdc.check_due(NOW - timedelta(hours=23), NOW)
    assert ncdc.check_due(NOW - timedelta(hours=25), NOW)


def test_the_button_waits_between_checks():
    assert not ncdc.may_check_now(NOW - timedelta(minutes=5), NOW)
    assert ncdc.may_check_now(NOW - timedelta(minutes=11), NOW)
    assert ncdc.may_check_now(None, NOW)


def test_the_listing_is_read_over_https_from_ncdc():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=LISTING)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = asyncio.run(ncdc.fetch_latest(client=client))
    assert seen == [ncdc.LISTING_URL] and r.week == 32


def test_a_report_larger_than_the_model_can_read_is_not_downloaded_whole():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF" + b"0" * (ncdc.MAX_PDF_BYTES + 10))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ncdc.NcdcError):
        asyncio.run(ncdc.fetch_pdf("https://ncdc.mohfw.gov.in/x.pdf", client=client))


def test_only_ncdc_addresses_are_fetched():
    with pytest.raises(ncdc.NcdcError):
        asyncio.run(ncdc.fetch_pdf("https://example.com/x.pdf"))


# ------------------------------------------------------------------ the API

from fastapi import BackgroundTasks, HTTPException  # noqa: E402

from app import api  # noqa: E402
from app.auth import Principal  # noqa: E402
from app.models import Event, IdspReport  # noqa: E402

OFFICER = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)
PHARMACIST = Principal(6, "ph@health.example", "Pharmacist", "facility_user", "MH", "Nashik", "F1", None)


class Session:
    def __init__(self, scalars):
        self._scalars = list(scalars)

    async def scalar(self, *_a, **_k):
        return self._scalars.pop(0)


def _checked(minutes_ago: float) -> Event:
    return Event(kind="idsp.checked", payload={"status": "up_to_date"},
                 created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


def test_opening_the_panel_checks_ncdc_when_the_last_check_is_over_a_day_old():
    tasks = BackgroundTasks()
    out = asyncio.run(api.ncdc_status(tasks, session=Session([_checked(60 * 30)]), user=OFFICER))
    assert out.checking is True and len(tasks.tasks) == 1


def test_a_recent_check_is_not_repeated_on_every_page_view():
    tasks = BackgroundTasks()
    out = asyncio.run(api.ncdc_status(tasks, session=Session([_checked(60)]), user=OFFICER))
    assert out.checking is False and tasks.tasks == []
    assert out.result == {"status": "up_to_date"}


def test_the_button_is_for_officers_and_waits_between_presses():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.ncdc_check(session=Session([]), user=PHARMACIST))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.ncdc_check(session=Session([_checked(3)]), user=OFFICER))
    assert exc.value.status_code == 429


def test_a_week_already_read_is_not_fetched_again(monkeypatch):
    recorded: list = []

    async def latest(**_k):
        return ncdc.Listed(2026, 32, ncdc.BASE + "/uploads/weekly_outbreaks/2026/week32_1790680151.pdf",
                           date(2026, 9, 29))

    async def never(*_a, **_k):
        raise AssertionError("fetched a report that was already read")

    async def record(session, kind, payload, state_silo=None):
        recorded.append((kind, payload))

    monkeypatch.setattr(ncdc, "fetch_latest", latest)
    monkeypatch.setattr(ncdc, "fetch_pdf", never)
    monkeypatch.setattr(api.events, "record", record)
    last = IdspReport(id=1, sha256="x", year=2026, week=32, model="m", rows=[])
    out = asyncio.run(api.run_ncdc_check(Session([last]), "MH officer"))
    assert out["status"] == "up_to_date" and out["week"] == 32
    assert recorded[0][0] == "idsp.checked"


def test_an_unreachable_ncdc_is_recorded_not_hidden(monkeypatch):
    recorded: list = []

    async def down(**_k):
        raise ncdc.NcdcError("NCDC's listing could not be read")

    async def record(session, kind, payload, state_silo=None):
        recorded.append(payload)

    monkeypatch.setattr(ncdc, "fetch_latest", down)
    monkeypatch.setattr(api.events, "record", record)
    out = asyncio.run(api.run_ncdc_check(Session([]), "MH officer"))
    assert out["status"] == "unreachable" and recorded[0]["status"] == "unreachable"

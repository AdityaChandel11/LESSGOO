"""Live IDSP intake — fix list #57.

NCDC publishes each week's IDSP Weekly Outbreak Report as a PDF listed at
LISTING_URL. This module reads the listing, finds the newest week, and fetches
that PDF only when it is newer than the last report the model read (#42).

Aditya's decision: check-on-use plus a button, never a scheduler. Opening the
outbreak panel checks when the last check is over a day old; an officer can
press "Check NCDC now", at most once every few minutes. Every check is
recorded as an event, so the panel can say when NCDC was last checked. The
model reads a report once: idsp_reports is keyed by the PDF's hash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

BASE = "https://ncdc.mohfw.gov.in"
LISTING_URL = f"{BASE}/includes/WeeklyOutbreaks.php"
REPORT_LINK = re.compile(r"uploads/weekly_outbreaks/(\d{4})/week(\d{1,2})_(\d{10})\.pdf", re.I)
# The model's inline limit (vision.MAX_REPORT_BYTES); NCDC's reports are ~1.2 MB.
MAX_PDF_BYTES = 12 * 1024 * 1024
TIMEOUT_S = 30.0
CHECK_EVERY = timedelta(hours=24)
BUTTON_COOLDOWN = timedelta(minutes=10)


class NcdcError(Exception):
    """NCDC could not be read; the caller says so and records nothing new."""


@dataclass(frozen=True)
class Listed:
    year: int
    week: int
    url: str
    uploaded_on: date


def latest_report(html: str) -> Listed | None:
    """The newest weekly report the listing links to."""
    found = [
        Listed(
            year=int(m.group(1)),
            week=int(m.group(2)),
            url=f"{BASE}/{m.group(0)}",
            uploaded_on=datetime.fromtimestamp(int(m.group(3)), timezone.utc).date(),
        )
        for m in REPORT_LINK.finditer(html)
    ]
    return max(found, key=lambda r: (r.year, r.week), default=None)


def is_newer(week: tuple[int, int], last: tuple[int, int] | None) -> bool:
    return last is None or week > last


def check_due(last_check: datetime | None, now: datetime) -> bool:
    return last_check is None or now - last_check > CHECK_EVERY


def may_check_now(last_check: datetime | None, now: datetime) -> bool:
    return last_check is None or now - last_check > BUTTON_COOLDOWN


def _client(client: httpx.AsyncClient | None) -> tuple[httpx.AsyncClient, bool]:
    if client is not None:
        return client, False
    return (
        httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_S),
            follow_redirects=True,
            headers={"User-Agent": "SwasthSetu prototype (IDSP intake)"},
        ),
        True,
    )


async def fetch_latest(*, client: httpx.AsyncClient | None = None) -> Listed | None:
    http, own = _client(client)
    try:
        response = await http.get(LISTING_URL)
        response.raise_for_status()
        return latest_report(response.text)
    except httpx.HTTPError as exc:
        raise NcdcError("NCDC's listing could not be read") from exc
    finally:
        if own:
            await http.aclose()


async def fetch_pdf(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """One report, from NCDC only, refused past the model's size limit."""
    if not url.startswith(f"{BASE}/uploads/weekly_outbreaks/"):
        raise NcdcError("Only NCDC's weekly outbreak reports are fetched")
    http, own = _client(client)
    try:
        async with http.stream("GET", url) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PDF_BYTES:
                    raise NcdcError("The report is too large to read in one request")
        if not bytes(body[:4]) == b"%PDF":
            raise NcdcError("NCDC returned something other than a PDF")
        return bytes(body)
    except httpx.HTTPError as exc:
        raise NcdcError("The report could not be downloaded from NCDC") from exc
    finally:
        if own:
            await http.aclose()

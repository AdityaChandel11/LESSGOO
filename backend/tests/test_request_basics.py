"""Stock requests between centres: a reply window, cancel, and plain words.

Fix list #31. A request nobody answered stayed "open" for ever, and the
per-centre cap counted it, so Nashik PHC 1 showed "3 of 3 stock requests
open" and could ask for nothing — while the cap's message told it to
"confirm or cancel one", and there was no cancel. The receipt printed raw
values ("proposed", "donor_facility") and a flat delivery date for a request
nobody had accepted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import api, auth, redistribution, workspace
from app.auth import Principal
from app.models import Facility, Transfer

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------- the reply window ---


def test_a_request_waits_thirty_minutes_for_a_reply_in_the_demo(monkeypatch):
    monkeypatch.setattr(redistribution.settings, "demo_mode", True)
    assert redistribution.request_window() == timedelta(minutes=30)


def test_and_a_day_in_real_use(monkeypatch):
    monkeypatch.setattr(redistribution.settings, "demo_mode", False)
    assert redistribution.request_window() == timedelta(hours=24)


def test_a_request_lapses_when_its_window_has_passed():
    window = timedelta(minutes=30)
    assert redistribution.request_lapsed(NOW - timedelta(minutes=31), NOW, window)
    assert redistribution.request_lapsed(NOW - timedelta(minutes=30), NOW, window)
    assert not redistribution.request_lapsed(NOW - timedelta(minutes=29), NOW, window)


# ---------------------------------------------------------- plain words ---


@pytest.mark.parametrize("status,lapsed,words", [
    ("proposed", False, "Awaiting reply from Nashik PHC 13 (pharmacist)"),
    ("proposed", True, "No reply from Nashik PHC 13"),
    ("approved", False, "Accepted and sent by Nashik PHC 13"),
    ("rejected", False, "Declined by Nashik PHC 13"),
    ("cancelled", False, "Cancelled by this centre"),
])
def test_a_request_status_is_said_in_words(status, lapsed, words):
    assert workspace.request_words(status, lapsed, "Nashik PHC 13") == words


def test_no_raw_value_ever_reaches_the_words():
    for status in ("proposed", "approved", "rejected", "cancelled", "completed"):
        text = workspace.request_words(status, False, "Nashik PHC 13")
        assert status not in text.split() and "donor_facility" not in text


# ------------------------------------------------------------- the caps ---

LIMITS = workspace.CapLimits(per_facility=3, overall=100)


def test_the_cap_message_offers_what_actually_exists():
    reason = workspace.check_caps(3, 10, LIMITS).reason
    assert "waiting for a reply" in reason
    assert "Cancel one" in reason


def test_the_global_cap_is_a_daily_ceiling():
    reason = workspace.check_caps(0, 100, LIMITS).reason
    assert "last 24 hours" in reason


# ----------------------------------------------------------- the estimate ---


def test_an_estimate_names_the_approval_time_it_depends_on():
    est = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 30, 9, 0, tzinfo=IST))
    assert est.approve_by == datetime(2026, 9, 30, 14, 0, tzinfo=IST)


def test_after_the_cutoff_the_approval_can_come_tomorrow():
    est = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 30, 16, 0, tzinfo=IST))
    assert est.approve_by == datetime(2026, 10, 1, 14, 0, tzinfo=IST)


def test_the_cutoff_is_read_in_india_time_not_utc():
    """10:00 UTC is 15:30 in India: past the 14:00 cutoff, so the vehicle
    leaves tomorrow. Reading the UTC hour (10) said it would leave today."""
    raised = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
    est = workspace.delivery_estimate(km=42.0, raised_at=workspace.in_india(raised))
    assert est.approve_by.date() == datetime(2026, 10, 1).date()


# ------------------------------------------------------------ cancelling ---

PHC1 = Facility(id="HFR-MH-PHC-00001", name="Nashik PHC 1", type="PHC", state_silo="MH", district="Nashik", lat=0.0, lng=0.0)
OWNER = Principal(6, "ph@health.example", "Pharmacist", "facility_user", "MH", "Nashik", "HFR-MH-PHC-00001", None)
OTHER = Principal(8, "ph13@health.example", "Pharmacist", "facility_user", "MH", "Nashik", "HFR-MH-PHC-00013", None)
OFFICER = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)


class Reached(Exception):
    pass


class FakeSession:
    def __init__(self, *rows):
        self._rows = {(type(r), r.id): r for r in rows}

    async def get(self, model, key, **_):
        if (model, key) in self._rows:
            return self._rows[(model, key)]
        raise Reached(f"get {model.__name__} {key}")

    async def scalar(self, *_a, **_k):
        raise Reached("scalar")

    async def execute(self, *_a, **_k):
        raise Reached("execute")

    def add(self, _row):
        pass

    async def commit(self):
        raise Reached("commit")


def _request() -> Transfer:
    return Transfer(
        id=77, from_facility="HFR-MH-PHC-00013", to_facility=PHC1.id, sku_code="ORS", qty=50,
        status="proposed", triggered_by=workspace.FACILITY_REQUEST, created_at=NOW,
    )


@pytest.fixture(autouse=True)
def real_accounts(monkeypatch):
    monkeypatch.setattr(auth.settings, "demo_mode", True)


def test_the_centre_that_raised_a_request_can_cancel_it():
    with pytest.raises(Reached):
        asyncio.run(api.cancel_request(77, session=FakeSession(_request(), PHC1), user=OWNER))


@pytest.mark.parametrize("who", [OTHER, OFFICER])
def test_nobody_else_can_cancel_it(who):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.cancel_request(77, session=FakeSession(_request(), PHC1), user=who))
    assert exc.value.status_code == 403


def test_a_solver_proposal_is_not_a_request_to_cancel():
    proposal = _request()
    proposal.triggered_by = "threshold"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.cancel_request(77, session=FakeSession(proposal, PHC1), user=OWNER))
    assert exc.value.status_code == 409

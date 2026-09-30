"""The field simulator sits inside the PHC workspace, bound to the centre.

Fix list #24. The workspace's "Away from a screen" link was `href="/?view=field"`:
a full page load that dropped a signed-in pharmacist on the front door, and a
view that only the officer console had anyway. The simulator now opens inside
the workspace, and a centre's staff may send only as their own centre's
registered handsets. A handset writes facts about its centre (stock, a
delivery, a check-in, beds), so #74's rule decides it: the centre itself, or
the labelled demo-sandbox exception, and nobody else.

The loop is closed in the reply: the reading row written, days of cover before
and after, the status change, and the event the district map reads.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import api, auth, ingest
from app.auth import Principal
from app.models import Facility, FacilityContact

OWN_ID = "HFR-MH-PHC-00001"
PHARMACIST = Principal(6, "ph@health.example", "Pharmacist", "facility_user", "MH", "Nashik", OWN_ID, None)
DEMO_PHARMACIST = Principal(
    8, "pharmacist@demo.swasthsetu.in", "Pharmacist", "facility_user", "MH", "Nashik", OWN_ID, None
)
REAL_STATE = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)
DEMO_DISTRICT = Principal(7, "nashik.ddlo@demo.swasthsetu.in", "Nashik DLO", "block_mo", "MH", "Nashik", None, None)


def _facility(fid: str, district: str) -> Facility:
    return Facility(id=fid, name=fid, type="PHC", state_silo="MH", district=district, lat=0.0, lng=0.0)


OWN = _facility(OWN_ID, "Nashik")
NEIGHBOUR = _facility("HFR-MH-PHC-00013", "Nashik")

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO / "frontend" / "src" / "workspace"


class Reached(Exception):
    """The request got past every permission check."""


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


@pytest.fixture(autouse=True)
def demo_on(monkeypatch):
    monkeypatch.setattr(auth.settings, "demo_mode", True)


@pytest.fixture
def spine(monkeypatch):
    """ingest.process stood in: reaching it means the guard let the send through."""

    async def process(session, submission):
        raise Reached("ingest.process")

    monkeypatch.setattr(ingest, "process", process)


def _registered_to(facility: Facility | None):
    async def identify(session, sender_ref):
        if facility is None:
            return None
        return FacilityContact(facility_id=facility.id, role="reporter")

    return identify


def refused(coro) -> str:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 403, exc.value.detail
    return exc.value.detail


def passes_guard(coro) -> None:
    try:
        asyncio.run(coro)
    except Reached:
        return
    except HTTPException as exc:
        assert exc.status_code != 403, exc.detail


def _send(user: Principal, *rows) -> object:
    body = api.SimulateIn(sender="9123456789", text="ORS 60")
    return api.ingest_simulate(body, session=FakeSession(*rows), user=user)


# ------------------------------------------------- sending as a handset ---


@pytest.mark.parametrize("user", [PHARMACIST, DEMO_PHARMACIST])
def test_a_pharmacist_sends_as_their_own_centres_handset(monkeypatch, spine, user):
    monkeypatch.setattr(ingest, "identify", _registered_to(OWN))
    passes_guard(_send(user, OWN))


@pytest.mark.parametrize("user", [PHARMACIST, DEMO_PHARMACIST])
def test_a_pharmacist_cannot_send_as_another_centres_handset(monkeypatch, spine, user):
    """Even a neighbour in the same district, inside the demo sandbox."""
    monkeypatch.setattr(ingest, "identify", _registered_to(NEIGHBOUR))
    detail = refused(_send(user, NEIGHBOUR))
    assert "your own centre" in detail


def test_the_refusal_does_not_say_whose_number_it_is(monkeypatch, spine):
    monkeypatch.setattr(ingest, "identify", _registered_to(NEIGHBOUR))
    assert NEIGHBOUR.id not in refused(_send(PHARMACIST, NEIGHBOUR))


def test_a_real_officer_cannot_send_as_a_centres_handset(monkeypatch, spine):
    """#74: a handset states facts about its centre, and only the centre may."""
    monkeypatch.setattr(ingest, "identify", _registered_to(NEIGHBOUR))
    refused(_send(REAL_STATE, NEIGHBOUR))


def test_the_labelled_demo_exception_still_works_inside_the_sandbox(monkeypatch, spine):
    monkeypatch.setattr(ingest, "identify", _registered_to(NEIGHBOUR))
    passes_guard(_send(DEMO_DISTRICT, NEIGHBOUR))


def test_an_unregistered_number_reaches_the_spine_which_refuses_it(monkeypatch, spine):
    """No centre owns it, so there is nothing to guard: the spine's identify
    stage answers "not registered" and writes nothing."""
    monkeypatch.setattr(ingest, "identify", _registered_to(None))
    passes_guard(_send(PHARMACIST))


# --------------------------------------------------- the handset list ---


def test_a_pharmacist_is_offered_their_own_centres_handsets():
    passes_guard(api.facility_handsets(OWN.id, session=FakeSession(OWN), user=PHARMACIST))


@pytest.mark.parametrize("user", [PHARMACIST, DEMO_PHARMACIST])
def test_a_pharmacist_is_not_offered_another_centres_handsets(user):
    refused(api.facility_handsets(NEIGHBOUR.id, session=FakeSession(NEIGHBOUR), user=user))


def test_a_real_officer_is_not_offered_a_centres_handsets():
    refused(api.facility_handsets(NEIGHBOUR.id, session=FakeSession(NEIGHBOUR), user=REAL_STATE))


# ----------------------------------------------------- closing the loop ---


def test_the_reply_carries_the_loop():
    """Row written, cover before and after, status change, event id."""
    out = api.SimulatedReadingOut.model_fields
    for name in ("reading_id", "qty_before", "days_before", "status_before", "days_of_stock", "status"):
        assert name in out, name
    assert "event_id" in api.SimulateOut.model_fields
    assert "written" in api.SimulateOut.model_fields


def test_a_reading_carries_what_the_map_showed_before():
    r = ingest.Reading(sku_code="ORS", sku_name="ORS", qty=60)
    assert r.reading_id is None and r.days_before is None and r.status_before is None
    assert ingest.Outcome(True, "ok", "committed").event_id is None


# ------------------------------------------- the entry does not reload ---


def _source(name: str) -> str:
    return (WORKSPACE / name).read_text(encoding="utf-8")


def test_the_medicines_screen_has_no_link_that_reloads_the_page():
    src = _source("Medicines.tsx")
    assert "?view=field" not in src
    assert not re.search(r"<a\b[^>]*href=", src), "an <a href> is a page load"
    assert "window.location" not in src


def test_the_entry_is_a_button_that_opens_the_simulator_in_place():
    src = _source("Medicines.tsx")
    assert re.search(r"<button[^>]*onClick=\{onOpenField\}", src, re.S)


def test_the_workspace_renders_the_simulator_bound_to_its_own_centre():
    src = _source("Shell.tsx")
    assert re.search(r"<FieldSimulator\b[^>]*facilityId=\{facilityId\}", src, re.S)
    assert "onOpenField=" in src
    assert "window.location" not in src

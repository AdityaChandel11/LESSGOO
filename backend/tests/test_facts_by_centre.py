"""Only the centre itself states facts about the centre.

Fix list #74. A stock level, a delivery's arrival, a staff check-in and a
ward's bed count are facts a centre reports about itself; the two-sided ledger
and the attendance checks exist precisely because the other side cannot settle
them. Officers could: an administrator could confirm any centre's delivery,
check its staff in, and send its ward photo. Now officers and administrators
view, and "Chase" — a reminder to the centre over the channel simulator.

One labelled exception: a public demo account may act for a centre inside the
demo sandbox, which is what the emergency drill and the console's demo buttons
do. Outside the sandbox, or on a real account, never.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app import api, auth
from app.auth import Principal
from app.models import Facility, MedicineMovement

REAL_ADMIN = Principal(3, "admin@health.example", "Admin", "admin", None, None, None, None)
REAL_STATE = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)
REAL_DISTRICT = Principal(5, "nsk@health.example", "Nashik DLO", "block_mo", "MH", "Nashik", None, None)
PHARMACIST = Principal(6, "ph@health.example", "Pharmacist", "facility_user", "MH", "Nashik", "HFR-MH-PHC-00001", None)
DEMO_ADMIN = Principal(1, "admin@demo.swasthsetu.in", "Platform Admin", "admin", None, None, None, None)
DEMO_DISTRICT = Principal(7, "nashik.ddlo@demo.swasthsetu.in", "Nashik DLO", "block_mo", "MH", "Nashik", None, None)


def _facility(fid: str, district: str) -> Facility:
    return Facility(id=fid, name=fid, type="PHC", state_silo="MH", district=district, lat=0.0, lng=0.0)


OWN = _facility("HFR-MH-PHC-00001", "Nashik")
NASHIK = _facility("HFR-MH-PHC-00013", "Nashik")
PUNE = _facility("HFR-MH-PHC-00200", "Pune")


class Reached(Exception):
    """The request got past every permission check and touched the database."""


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

    async def rollback(self):
        pass


@pytest.fixture(autouse=True)
def demo_on(monkeypatch):
    monkeypatch.setattr(auth.settings, "demo_mode", True)


def refused(coro, *words: str) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 403, exc.value.detail
    for w in words:
        assert w in exc.value.detail


def passes_guard(coro) -> None:
    try:
        asyncio.run(coro)
    except Reached:
        return
    except HTTPException as exc:
        assert exc.status_code != 403, exc.detail


# ------------------------------------------------------------ the rule ---


def _facts(p: Principal, f: Facility) -> bool:
    return auth.can_report_facts(p, facility_id=f.id, facility_state=f.state_silo, facility_district=f.district)


def test_a_centre_reports_its_own_facts():
    assert _facts(PHARMACIST, OWN)


def test_a_centre_cannot_report_for_another_centre():
    assert not _facts(PHARMACIST, NASHIK)


@pytest.mark.parametrize("officer", [REAL_ADMIN, REAL_STATE, REAL_DISTRICT])
def test_an_officer_reports_no_facts_for_a_centre(officer):
    assert not _facts(officer, NASHIK)
    assert not _facts(officer, PUNE)


def test_a_demo_account_may_act_for_a_centre_inside_the_sandbox():
    assert _facts(DEMO_ADMIN, NASHIK)
    assert _facts(DEMO_DISTRICT, NASHIK)


def test_but_never_outside_it():
    assert not _facts(DEMO_ADMIN, PUNE)


def test_and_never_once_demo_mode_is_off(monkeypatch):
    monkeypatch.setattr(auth.settings, "demo_mode", False)
    assert not _facts(DEMO_ADMIN, NASHIK)


def test_viewing_a_centre_keeps_the_officers_scope():
    """Narrowing who may report must not lock officers out of looking."""
    assert auth.can_view_facility(
        REAL_STATE, facility_id=PUNE.id, facility_state="MH", facility_district="Pune"
    )


# --------------------------------------------------- the four endpoints ---

ONLY_THE_CENTRE = "only the centre"


@pytest.mark.parametrize("officer", [REAL_ADMIN, REAL_STATE, REAL_DISTRICT])
def test_an_officer_cannot_report_a_centres_stock(officer):
    body = api.StockReadingIn(facility_id=NASHIK.id, sku_code="ORS", qty_on_hand=4)
    refused(api.submit_reading(body, session=FakeSession(NASHIK), user=officer), ONLY_THE_CENTRE)


def test_an_officer_cannot_photograph_a_centres_stock():
    body = api.StockPhotoIn(image_base64="aGVsbG8=")
    refused(api.submit_stock_photo(NASHIK.id, body, session=FakeSession(NASHIK), user=REAL_STATE), ONLY_THE_CENTRE)


@pytest.mark.parametrize("officer", [REAL_ADMIN, REAL_STATE, REAL_DISTRICT])
def test_an_officer_cannot_confirm_a_centres_delivery(officer):
    session = FakeSession(NASHIK, MedicineMovement(id=41, to_facility=NASHIK.id, sku_code="ORS"))
    refused(api.confirm_receipt(41, api.ReceiptIn(qty_received=10), session=session, user=officer), ONLY_THE_CENTRE)


@pytest.mark.parametrize("officer", [REAL_ADMIN, REAL_STATE, REAL_DISTRICT])
def test_an_officer_cannot_check_a_centres_staff_in(officer):
    body = api.CheckinIn(staff_ref="S1")
    refused(api.submit_checkin(NASHIK.id, body, session=FakeSession(NASHIK), user=officer), ONLY_THE_CENTRE)


@pytest.mark.parametrize("officer", [REAL_ADMIN, REAL_STATE, REAL_DISTRICT])
def test_an_officer_cannot_report_a_centres_beds(officer):
    refused(api.submit_bed_report(NASHIK.id, api.BedReportIn(), session=FakeSession(NASHIK), user=officer), ONLY_THE_CENTRE)


def test_the_centre_itself_still_reports():
    body = api.StockReadingIn(facility_id=OWN.id, sku_code="ORS", qty_on_hand=4)
    passes_guard(api.submit_reading(body, session=FakeSession(OWN), user=PHARMACIST))
    session = FakeSession(OWN, MedicineMovement(id=41, to_facility=OWN.id, sku_code="ORS"))
    passes_guard(api.confirm_receipt(41, api.ReceiptIn(qty_received=10), session=session, user=PHARMACIST))
    passes_guard(api.submit_checkin(OWN.id, api.CheckinIn(staff_ref="S1"), session=FakeSession(OWN), user=PHARMACIST))


def test_the_drill_still_runs_in_the_sandbox():
    """The emergency drill reports a stock level and confirms a delivery as the
    officer running it — the labelled exception, inside the sandbox only."""
    body = api.StockReadingIn(facility_id=NASHIK.id, sku_code="ORS", qty_on_hand=4, source="sms")
    passes_guard(api.submit_reading(body, session=FakeSession(NASHIK), user=DEMO_ADMIN))
    session = FakeSession(NASHIK, MedicineMovement(id=41, to_facility=NASHIK.id, sku_code="ORS"))
    passes_guard(api.confirm_receipt(41, api.ReceiptIn(qty_received=10), session=session, user=DEMO_ADMIN))


def test_an_officer_can_still_read_todays_bed_code():
    """Reading is not reporting: the code screen stays open to the officer."""
    passes_guard(api.bed_code(NASHIK.id, session=FakeSession(NASHIK), user=REAL_DISTRICT))


# ------------------------------------------------------------- chase ---


def test_an_officer_chases_a_centre_in_their_area():
    body = api.ChaseIn(topic="checkin")
    passes_guard(api.chase_facility(NASHIK.id, body, session=FakeSession(NASHIK), user=REAL_DISTRICT))


def test_a_centre_does_not_chase_itself():
    body = api.ChaseIn(topic="checkin")
    refused(api.chase_facility(OWN.id, body, session=FakeSession(OWN), user=PHARMACIST))


def test_a_demo_account_chases_only_inside_the_sandbox():
    body = api.ChaseIn(topic="beds")
    refused(api.chase_facility(PUNE.id, body, session=FakeSession(PUNE), user=DEMO_ADMIN), "sandbox")


@pytest.mark.parametrize("topic,words", [
    ("receipt", ["GOT WH-7", "counted"]),
    ("checkin", ["IN"]),
    ("beds", ["BEDS"]),
])
def test_each_reminder_says_what_to_send_back(topic, words):
    text = api.chase_text(topic, "Nashik PHC 13", batch="WH-7", medicine="ORS")
    for w in words:
        assert w in text


def test_a_delivery_reminder_does_not_hand_over_the_number_to_confirm():
    """The receiver counts before seeing the dispatched figure (fix #75's rule):
    a reminder that says "reply GOT WH-7 250" invites confirming in full, so
    the reminder is not even given the dispatched quantity."""
    text = api.chase_text("receipt", "Nashik PHC 13", batch="WH-7", medicine="ORS")
    assert "250" not in text

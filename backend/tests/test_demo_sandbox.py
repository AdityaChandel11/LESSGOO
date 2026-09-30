"""Public demo accounts may change things only inside the Nashik sandbox.

Fix list #82. The deployed site lets anyone in as a demo account, with no
password, on a free database that has about 100 MB to spare. Outside the
sandbox district those accounts are view-only; inside it the demo works as
before. Real accounts are not affected by any of this.

The endpoint tests call each route's coroutine directly, the way
test_demo_flows.py does. `FakeSession` answers only the lookups a route makes
*before* its permission checks, and stops the request at the first database
call after them — so "reached" means "got past every permission check".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app import api, auth, ingest, redistribution
from app.auth import Principal
from app.models import Facility, FacilityContact, MedicineMovement

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

DEMO_ADMIN = Principal(1, "admin@demo.swasthsetu.in", "Platform Admin", "admin", None, None, None, None)
DEMO_STATE = Principal(2, "mh.officer@demo.swasthsetu.in", "MH officer", "state_officer", "MH", None, None, None)
REAL_ADMIN = Principal(3, "admin@health.example", "Admin", "admin", None, None, None, None)
REAL_STATE = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)


def _facility(fid: str, state: str, district: str) -> Facility:
    return Facility(id=fid, name=fid, type="PHC", state_silo=state, district=district, lat=0.0, lng=0.0)


NASHIK = _facility("HFR-MH-PHC-00001", "MH", "Nashik")
NASHIK_B = _facility("HFR-MH-PHC-00013", "MH", "Nashik")
PUNE = _facility("HFR-MH-PHC-00200", "MH", "Pune")
JAIPUR = _facility("HFR-RJ-PHC-00007", "RJ", "Jaipur")
JAIPUR_B = _facility("HFR-RJ-PHC-00008", "RJ", "Jaipur")


class Reached(Exception):
    """The request got past every permission check and touched the database."""


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, *rows, executes: list[list] | None = None):
        self._rows = {(type(r), r.id): r for r in rows}
        self._executes = list(executes or [])

    async def get(self, model, key, **_):
        if (model, key) in self._rows:
            return self._rows[(model, key)]
        raise Reached(f"get {model.__name__} {key}")

    async def scalar(self, *_a, **_k):
        raise Reached("scalar")

    async def execute(self, *_a, **_k):
        if self._executes:
            return _Result(self._executes.pop(0))
        raise Reached("execute")

    async def rollback(self):
        pass


@pytest.fixture(autouse=True)
def demo_on(monkeypatch):
    monkeypatch.setattr(auth.settings, "demo_mode", True)


def refused(coro) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 403, exc.value.detail
    assert "Nashik sandbox" in exc.value.detail


def passes_guard(coro) -> None:
    """Anything but a 403: the request went on to do its real work."""
    try:
        asyncio.run(coro)
    except Reached:
        return
    except HTTPException as exc:
        assert exc.status_code != 403, exc.detail


# ------------------------------------------------------------ the rule ---


def test_a_demo_account_is_recognised_by_its_domain_only_in_demo_mode(monkeypatch):
    assert auth.is_public_demo(DEMO_ADMIN)
    assert not auth.is_public_demo(REAL_ADMIN)
    monkeypatch.setattr(auth.settings, "demo_mode", False)
    assert not auth.is_public_demo(DEMO_ADMIN)


def test_a_demo_account_may_write_only_inside_the_sandbox():
    assert auth.demo_may_write(DEMO_ADMIN, state="MH", district="Nashik")
    assert not auth.demo_may_write(DEMO_ADMIN, state="MH", district="Pune")
    assert not auth.demo_may_write(DEMO_ADMIN, state="RJ", district="Jaipur")


def test_a_real_account_is_not_confined_to_the_sandbox():
    assert auth.demo_may_write(REAL_ADMIN, state="RJ", district="Jaipur")
    assert auth.demo_may_plan(REAL_ADMIN, "RJ")


def test_a_demo_account_may_plan_only_the_sandbox_state():
    assert auth.demo_may_plan(DEMO_ADMIN, "MH")
    assert not auth.demo_may_plan(DEMO_ADMIN, "RJ")


def test_the_next_full_plan_waits_out_the_interval():
    last = NOW - timedelta(minutes=4)
    assert auth.next_full_plan_at(last, NOW, 10) == last + timedelta(minutes=10)
    assert auth.next_full_plan_at(NOW - timedelta(minutes=11), NOW, 10) is None
    assert auth.next_full_plan_at(None, NOW, 10) is None


def test_the_session_tells_a_demo_account_where_its_sandbox_is():
    sandbox = auth._session_out(DEMO_ADMIN).user.demo_sandbox
    assert sandbox is not None
    assert (sandbox.state, sandbox.district) == ("MH", "Nashik")


def test_a_real_account_has_no_sandbox():
    assert auth._session_out(REAL_ADMIN).user.demo_sandbox is None


# ------------------------------------------------ facts about a centre ---


def test_a_demo_stock_report_outside_the_sandbox_is_refused():
    body = api.StockReadingIn(facility_id=PUNE.id, sku_code="ORS", qty_on_hand=4)
    refused(api.submit_reading(body, session=FakeSession(PUNE), user=DEMO_STATE))


def test_a_demo_stock_report_inside_the_sandbox_goes_through():
    body = api.StockReadingIn(facility_id=NASHIK.id, sku_code="ORS", qty_on_hand=4)
    passes_guard(api.submit_reading(body, session=FakeSession(NASHIK), user=DEMO_STATE))


def test_a_real_admin_is_not_confined_by_the_demo_rule():
    # A stock request, not a stock report: since fix #74 no officer reports a
    # centre's facts at all, so that endpoint cannot show this rule on its own.
    body = api.RequestIn(sku_code="ORS", from_facility=JAIPUR_B.id, qty=10)
    passes_guard(api.create_request(JAIPUR.id, body, session=FakeSession(JAIPUR), user=REAL_ADMIN))


def test_a_demo_stock_photo_outside_the_sandbox_is_refused():
    body = api.StockPhotoIn(image_base64="aGVsbG8=")
    refused(api.submit_stock_photo(PUNE.id, body, session=FakeSession(PUNE), user=DEMO_STATE))


def test_a_demo_receipt_outside_the_sandbox_is_refused():
    session = FakeSession(PUNE, MedicineMovement(id=41, to_facility=PUNE.id, sku_code="ORS"))
    refused(api.confirm_receipt(41, api.ReceiptIn(qty_received=10), session=session, user=DEMO_STATE))


def test_a_demo_bed_report_outside_the_sandbox_is_refused():
    refused(api.submit_bed_report(PUNE.id, api.BedReportIn(), session=FakeSession(PUNE), user=DEMO_STATE))


def test_a_demo_check_in_outside_the_sandbox_is_refused():
    body = api.CheckinIn(staff_ref="S1")
    refused(api.submit_checkin(PUNE.id, body, session=FakeSession(PUNE), user=DEMO_STATE))


# ---------------------------------------------------------- requests ---


def test_a_demo_stock_request_outside_the_sandbox_is_refused():
    body = api.RequestIn(sku_code="ORS", from_facility=NASHIK.id, qty=10)
    refused(api.create_request(PUNE.id, body, session=FakeSession(PUNE), user=DEMO_STATE))


def test_a_demo_neighbour_request_outside_the_sandbox_is_refused():
    refused(api.demo_neighbour_request(PUNE.id, session=FakeSession(PUNE), user=DEMO_STATE))


# ------------------------------------------------------------ decisions ---


def _one_transfer(src: Facility, dst: Facility):
    async def list_transfers(session, **_):
        return [{"from": {"id": src.id, "name": src.name}, "to": {"id": dst.id, "name": dst.name},
                 "sku_code": "ORS", "qty": 10.0}]
    return list_transfers


def test_a_demo_admin_cannot_decide_a_transfer_outside_the_sandbox(monkeypatch):
    monkeypatch.setattr(redistribution, "list_transfers", _one_transfer(JAIPUR, JAIPUR_B))
    session = FakeSession(executes=[[JAIPUR, JAIPUR_B]])
    refused(api._decide(7, "approved", session, DEMO_ADMIN))


def test_a_demo_admin_can_decide_a_transfer_inside_the_sandbox(monkeypatch):
    monkeypatch.setattr(redistribution, "list_transfers", _one_transfer(NASHIK, NASHIK_B))
    session = FakeSession(executes=[[NASHIK, NASHIK_B]])
    passes_guard(api._decide(7, "approved", session, DEMO_ADMIN))


# ---------------------------------------------------------------- plans ---


def test_a_demo_account_cannot_plan_another_state():
    refused(api.plan_transfers(api.PlanIn(state="RJ"), session=FakeSession(), user=DEMO_ADMIN))


async def _ran_minutes_ago(minutes: float):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


async def _at(when: datetime):
    return when


def test_a_second_full_demo_plan_inside_the_interval_is_refused(monkeypatch):
    # `since` is the endpoint's own clock minus the 10-minute interval, so
    # since + 6 minutes is exactly 4 minutes before the endpoint's "now".
    monkeypatch.setattr(
        api, "_last_full_plan_at", lambda session, state, since: _at(since + timedelta(minutes=6))
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.plan_transfers(api.PlanIn(state="MH"), session=FakeSession(), user=DEMO_STATE))
    assert exc.value.status_code == 429
    assert "try again in 6 minutes" in exc.value.detail
    assert exc.value.headers["Retry-After"] == "360"


def test_the_drills_single_medicine_plan_is_not_rate_limited(monkeypatch):
    monkeypatch.setattr(api, "_last_full_plan_at", lambda session, state, since: _ran_minutes_ago(0))
    passes_guard(api.plan_transfers(api.PlanIn(state="MH", sku="ORS"), session=FakeSession(), user=DEMO_STATE))


def test_a_real_officer_is_not_rate_limited(monkeypatch):
    monkeypatch.setattr(api, "_last_full_plan_at", lambda session, state, since: _ran_minutes_ago(0))
    passes_guard(api.plan_transfers(api.PlanIn(state="MH"), session=FakeSession(), user=REAL_STATE))


# ------------------------------------------------------------ simulator ---


def _handset_of(facility: Facility):
    async def identify(session, sender_ref):
        return FacilityContact(facility_id=facility.id, role="reporter")
    return identify


def test_a_demo_account_cannot_send_as_a_handset_outside_the_sandbox(monkeypatch):
    monkeypatch.setattr(ingest, "identify", _handset_of(PUNE))
    body = api.SimulateIn(sender="+919000000001", text="ORS 4")
    refused(api.ingest_simulate(body, session=FakeSession(PUNE), user=DEMO_STATE))


def test_a_demo_account_can_send_as_a_sandbox_handset(monkeypatch):
    monkeypatch.setattr(ingest, "identify", _handset_of(NASHIK))

    async def process(session, submission):
        raise Reached("ingest.process")

    monkeypatch.setattr(ingest, "process", process)
    body = api.SimulateIn(sender="+919000000001", text="ORS 4")
    passes_guard(api.ingest_simulate(body, session=FakeSession(NASHIK), user=DEMO_STATE))


def test_a_demo_account_gets_no_handsets_outside_the_sandbox():
    refused(api.facility_handsets(PUNE.id, session=FakeSession(PUNE), user=DEMO_STATE))


# ---------------------------------------------------- nothing forgotten ---

# Every route that writes, and why it is or is not behind the sandbox rule. A
# new write route fails this test until someone decides which list it joins.
GUARDED = {
    "/transfers/plan",
    "/transfers/{transfer_id}/approve",
    "/transfers/{transfer_id}/reject",
    "/transfers/{transfer_id}/cancel",
    "/facilities/{facility_id}/stock-photo",
    "/facilities/{facility_id}/demo-request",
    "/facilities/{facility_id}/requests",
    "/stock/readings",
    "/movements/{movement_id}/receipt",
    "/facilities/{facility_id}/bed-reports",
    "/facilities/{facility_id}/checkins",
    "/facilities/{facility_id}/chase",
    "/ingest/simulate",
}
EXEMPT = {
    "/transfers/explain": "writes nothing but a cache of the model's wording",
    "/facilities/{facility_id}/briefing": "one cached briefing row per centre, replaced in place",
    "/facilities/{facility_id}/trust/explain": "writes nothing but a cache of the model's wording",
    "/federation/live": "administrator only, and refuses to start in production",
}


def test_every_write_route_is_classified():
    writes = {
        r.path
        for r in api.router.routes
        if isinstance(r, APIRoute) and r.methods & {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert writes == GUARDED | set(EXEMPT)

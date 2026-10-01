"""Fix #77: data governance tiers, enforced in the API.

Facility-level rows stay with the state that holds them. The national role
sees state and district aggregates and model outputs; a state officer sees the
centres of their own state; a district officer those of their own district; a
centre sees itself. Anyone else asking for a centre's rows is told where they
are held instead of being handed them.

The challenge asks for national visibility, so aggregates are legitimate at
every level. What must not cross a state line is a row about one centre.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app import aggregates, api, auth, earlywarning, events, movements, outbreak, redistribution
from app.auth import Principal


def who(role, state=None, district=None, facility=None) -> Principal:
    return Principal(1, f"{role}@example.org", role, role, state, district, facility, None)


ADMIN = who("admin")
MH = who("state_officer", "MH")
UP = who("state_officer", "UP")
NASHIK = who("block_mo", "MH", "Nashik")
PHC1 = who("facility_user", "MH", "Nashik", "HFR-MH-PHC-00001")

NASHIK_PHC = dict(state="MH", district="Nashik", facility_id="HFR-MH-PHC-00001")
PUNE_PHC = dict(state="MH", district="Pune", facility_id="HFR-MH-PHC-00900")


class FakeSession:
    """`session.get(Facility, id)` for the one-centre routes; nothing else."""

    def __init__(self, facility=None):
        self.facility = facility

    async def get(self, model, key):
        return self.facility


def centre(state="MH", district="Nashik", fid="HFR-MH-PHC-00001"):
    return SimpleNamespace(id=fid, name="Nashik PHC 1", state_silo=state, district=district)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- the rule ---


@pytest.mark.parametrize(
    "person, where, allowed",
    [
        (ADMIN, NASHIK_PHC, False),   # national: aggregates only, everywhere
        (MH, NASHIK_PHC, True),
        (MH, PUNE_PHC, True),
        (UP, NASHIK_PHC, False),      # another state's officer
        (NASHIK, NASHIK_PHC, True),
        (NASHIK, PUNE_PHC, False),    # same state, another district
        (PHC1, NASHIK_PHC, True),
        (PHC1, dict(state="MH", district="Nashik", facility_id="HFR-MH-PHC-00013"), False),
    ],
)
def test_who_may_read_a_centres_rows(person, where, allowed):
    assert auth.can_read_facility_rows(person, **where) is allowed


DEMO_ADMIN = Principal(9, "admin@demo.swasthsetu.in", "Platform Admin", "admin", None, None, None, None)


def test_a_demo_account_reads_centres_inside_the_sandbox_and_nowhere_else(monkeypatch):
    # The same labelled exception every demo rule makes (fix #74, #82): the
    # drill runs in the sandbox district, so a demo administrator reads there.
    monkeypatch.setattr(auth.settings, "demo_mode", True)
    assert auth.can_read_facility_rows(DEMO_ADMIN, **NASHIK_PHC)
    assert not auth.can_read_facility_rows(DEMO_ADMIN, **PUNE_PHC)
    assert api._narrow_to_rows_scope(DEMO_ADMIN, None, None) == ("MH", "Nashik")
    assert api._narrow_to_rows_scope(DEMO_ADMIN, "MH", "Pune") is None
    assert api._narrow_to_rows_scope(DEMO_ADMIN, "UP", None) is None
    # A real national account has no such exception, and neither has a demo
    # account once demo mode is off.
    assert not auth.can_read_facility_rows(ADMIN, **NASHIK_PHC)
    monkeypatch.setattr(auth.settings, "demo_mode", False)
    assert not auth.can_read_facility_rows(DEMO_ADMIN, **NASHIK_PHC)
    assert api._narrow_to_rows_scope(DEMO_ADMIN, None, None) is None


def test_the_refusal_says_where_the_rows_are_held():
    assert auth.held_message(ADMIN, "MH") == (
        "Held in Maharashtra's store — the national view sees district summaries only."
    )
    other = auth.held_message(UP, "MH")
    assert other.startswith("Held in Maharashtra's store") and "Uttar Pradesh" in other
    district = auth.held_message(NASHIK, "MH")
    assert "Nashik" in district and "district summaries" in district


# --------------------------------------------------------- one centre ---


@pytest.mark.parametrize(
    "handler",
    ["get_facility", "facility_usage", "list_bed_reports", "facility_attendance", "facility_trust"],
)
def test_a_centre_outside_scope_is_refused_before_anything_is_read(handler):
    call = getattr(api, handler)
    kwargs = {"session": FakeSession(centre()), "user": ADMIN}
    if handler == "facility_usage":
        kwargs["sku"] = "ORS"
    with pytest.raises(HTTPException) as refused:
        run(call("HFR-MH-PHC-00001", **kwargs))
    assert refused.value.status_code == 403
    assert refused.value.detail == auth.held_message(ADMIN, "MH")


def test_an_unknown_centre_is_not_found_rather_than_refused():
    with pytest.raises(HTTPException) as missing:
        run(api.get_facility("HFR-XX-PHC-1", session=FakeSession(None), user=ADMIN))
    assert missing.value.status_code == 404


# ------------------------------------------------------------ the map ---


def pins_call(user, monkeypatch, **query):
    asked: dict = {}

    async def find(session, **kw):
        asked.update(kw)
        return []

    monkeypatch.setattr(aggregates, "find_facilities", find)
    base = dict(south=None, west=None, north=None, east=None, state=None, district=None,
                sku=None, status=None, limit=100)
    base.update(query)
    out = run(api.map_facilities(**base, session=object(), user=user))
    return out, asked


def test_the_national_role_gets_no_pins_and_the_database_is_not_asked(monkeypatch):
    out, asked = pins_call(ADMIN, monkeypatch, state="MH")
    assert out == [] and asked == {}


def test_a_state_officer_gets_pins_only_in_their_own_state(monkeypatch):
    out, asked = pins_call(MH, monkeypatch, south=18.0, west=72.0, north=21.0, east=80.0)
    assert asked["state"] == "MH"       # the viewport is narrowed to the state
    out, asked = pins_call(MH, monkeypatch, state="UP")
    assert out == [] and asked == {}    # another state: nothing, and no read


def test_a_district_officer_gets_pins_only_in_their_own_district(monkeypatch):
    _, asked = pins_call(NASHIK, monkeypatch, state="MH")
    assert (asked["state"], asked["district"]) == ("MH", "Nashik")
    out, asked = pins_call(NASHIK, monkeypatch, state="MH", district="Pune")
    assert out == [] and asked == {}


# ----------------------------------------------------------- transfers ---


def trip(tid, from_district="Nashik", to_district="Nashik", from_id="A", to_id="B"):
    return {
        "id": tid, "sku_code": "ORS", "sku_name": "ORS", "unit": "sachet", "qty": 10.0,
        "status": "proposed", "triggered_by": "solver", "created_at": datetime.now(timezone.utc),
        "from": {"id": from_id, "name": "x", "district": from_district, "lat": 0.0, "lng": 0.0},
        "to": {"id": to_id, "name": "y", "district": to_district, "lat": 0.0, "lng": 0.0},
    }


def test_transfer_rows_follow_the_same_scope(monkeypatch):
    asked: list = []

    async def listed(session, **kw):
        asked.append(kw)
        return [trip(1), trip(2, "Pune", "Pune"), trip(3, "Pune", "Nashik")]

    monkeypatch.setattr(redistribution, "list_transfers", listed)
    monkeypatch.setattr(api.TransferOut, "model_validate", staticmethod(lambda r: r["id"]))
    get = lambda user, state: run(  # noqa: E731
        api.get_transfers(state=state, status=None, limit=50, session=object(), user=user)
    )
    assert get(ADMIN, "MH") == [] and asked == []
    assert get(UP, "MH") == [] and asked == []
    assert get(MH, "MH") == [1, 2, 3] and asked[-1]["state"] == "MH"
    # A district officer sees the trips that touch their district.
    assert get(NASHIK, "MH") == [1, 3]


def test_a_trips_explanation_is_its_two_centres_rows(monkeypatch):
    async def listed(session, **kw):
        return [{**trip(1), "state": "MH"}]

    monkeypatch.setattr(redistribution, "list_transfers", listed)
    body = api.TripExplainIn(transfer_ids=[1])
    for outsider in (ADMIN, UP, who("block_mo", "MH", "Pune")):
        with pytest.raises(HTTPException) as refused:
            run(api.explain_trip(body, session=object(), user=outsider))
        assert refused.value.status_code == 403
        assert refused.value.detail == auth.held_message(outsider, "MH")


def test_oversight_keeps_its_counts_and_withholds_its_named_lists(monkeypatch):
    # The reply's real field names (redistribution.oversight).
    named = {
        "no_reply": [{"transfer_id": 9}], "not_received": [{"movement_id": 3}],
        "declined": [{"transfer_id": 4}], "controlled": [{"facility_id": "a"}],
        "unreached": [{"facility_id": "b"}],
    }

    async def overseen(session, state, now):
        return {
            "state": state, "pipeline": {"recommended": 4, "received": 1},
            "no_reply_total": 1, "not_received_total": 1, "controlled_total": 1,
            "unreached_total": 1, **named,
        }

    async def known(*a, **k):
        return "HFR-MH-PHC-00001"

    monkeypatch.setattr(redistribution, "oversight", overseen)
    session = SimpleNamespace(scalar=known)
    national = run(api.transfers_oversight(state="MH", session=session, user=ADMIN))
    assert national["pipeline"] == {"recommended": 4, "received": 1}
    assert all(national[k] == [] for k in named)
    assert (national["no_reply_total"], national["unreached_total"]) == (1, 1)
    assert national["rows_withheld"] == auth.held_message(ADMIN, "MH")
    # The same for another state's officer and for a district officer: the
    # lists are state-wide, so only the state's own officer reads them.
    for outsider in (UP, NASHIK):
        assert all(
            run(api.transfers_oversight(state="MH", session=session, user=outsider))[k] == []
            for k in named
        )
    own = run(api.transfers_oversight(state="MH", session=session, user=MH))
    assert own["no_reply"] == [{"transfer_id": 9}] and own["rows_withheld"] is None


def test_the_named_lists_in_the_test_above_are_the_real_ones():
    # Pinned to the source, so a list added to the oversight reply cannot be
    # forgotten here: every list it returns is emptied by the same rule.
    import inspect

    src = inspect.getsource(redistribution.oversight)
    for key in ("no_reply", "not_received", "declined", "controlled", "unreached"):
        assert f'"{key}": ' in src


# ----------------------------------------------- movements, trust, calls ---


def test_the_national_role_gets_the_ledgers_totals_and_none_of_its_rows(monkeypatch):
    async def rows(session, **kw):
        raise AssertionError("the national role must not read ledger rows")

    async def totals(session, **kw):
        return {"counts": {"short": 7}, "short_units": 120.0}

    monkeypatch.setattr(movements, "list_movements", rows)
    monkeypatch.setattr(movements, "summary", totals)
    out = run(api.list_movements(
        state="MH", district=None, facility=None, sku=None, view="attention",
        limit=50, offset=0, session=object(), user=ADMIN,
    ))
    assert out.movements == [] and out.counts == {"short": 7}
    assert out.rows_withheld == auth.held_message(ADMIN, "MH")


def test_the_audit_queue_is_refused_to_the_national_role():
    with pytest.raises(HTTPException) as refused:
        run(api.audit_queue(state="MH", district=None, limit=10, session=object(), user=ADMIN))
    assert refused.value.status_code == 403
    assert refused.value.detail == auth.held_message(ADMIN, "MH")


# --------------------------------------------------- warnings and events ---


def test_an_active_outbreaks_named_centres_are_withheld_and_counted(monkeypatch):
    row = SimpleNamespace(
        id=1, state_silo="MH", district="Nashik", disease_category="Cholera", source="officer",
        source_ref=None, surge_pct=50, declared_by="x", triggered_at=None, expires_at=None,
    )
    view = SimpleNamespace(row=row, ids=["a", "b"], medicines=[], overdue=0)
    warning = {
        "outbreak_id": 1, "facility_id": "a", "facility_name": "Nashik PHC 9", "district": "Nashik",
        "sku_code": "ORS", "sku_name": "ORS", "runs_out_on": date(2026, 10, 4),
        "runs_out_without": date(2026, 10, 19), "basis": "assumption", "line": "…",
    }

    async def active(session, now, state=None):
        return [row]

    async def evaluate(session, r, now):
        return view

    async def warnings(session, v, now):
        return [warning, {**warning, "facility_id": "b", "facility_name": "Nashik PHC 2"}]

    monkeypatch.setattr(outbreak, "active", active)
    monkeypatch.setattr(outbreak, "evaluate", evaluate)
    monkeypatch.setattr(outbreak, "warnings", warnings)
    national = run(api.active_outbreaks(state=None, session=object(), user=ADMIN)).outbreaks[0]
    assert national.warnings == [] and national.warnings_count == 2
    own = run(api.active_outbreaks(state=None, session=object(), user=NASHIK)).outbreaks[0]
    assert [w.facility_name for w in own.warnings] == ["Nashik PHC 9", "Nashik PHC 2"]


def test_the_next_14_days_strip_names_a_centre_only_to_those_who_hold_its_rows(monkeypatch):
    pair = earlywarning.Pair(
        state="MH", district="Nashik", sku_code="ORS", sku_name="Oral Rehydration Salts",
        centres=3, first_on=date(2026, 10, 4), first_centre="Nashik PHC 9", by_forecast=0,
    )

    async def strip(session, now, **kw):
        return earlywarning.Strip(14, now, [pair], 0, None, 8.0)

    monkeypatch.setattr(earlywarning, "strip", strip)
    call = lambda user: run(  # noqa: E731
        api.next_warnings(state=None, source="all", limit=8, session=object(), user=user)
    ).pairs[0]
    assert call(MH).first_centre == "Nashik PHC 9"
    national = call(ADMIN)
    assert national.first_centre is None and "Nashik PHC 9" not in national.line
    assert national.centres == 3 and national.first_on == date(2026, 10, 4)


def test_an_events_payload_is_withheld_outside_scope():
    where = {"HFR-MH-PHC-00001": ("MH", "Nashik"), "HFR-MH-PHC-00900": ("MH", "Pune")}
    feed = [
        {"id": 1, "kind": events.READING_COMMITTED, "created_at": None,
         "data": {"facility_id": "HFR-MH-PHC-00001", "facility_name": "Nashik PHC 1", "qty": 4}},
        {"id": 2, "kind": events.READING_COMMITTED, "created_at": None,
         "data": {"facility_id": "HFR-MH-PHC-00900", "facility_name": "Pune PHC 1", "qty": 9}},
        {"id": 3, "kind": events.FEDERATION_ROUND, "created_at": None, "data": {"round": 3}},
    ]
    national = api.scope_events(feed, where, ADMIN)
    assert [e["data"] for e in national] == [{"withheld": True}, {"withheld": True}, {"round": 3}]
    assert [e["id"] for e in national] == [1, 2, 3]          # the feed still ticks
    district = api.scope_events(feed, where, NASHIK)
    assert district[0]["data"]["facility_name"] == "Nashik PHC 1"
    assert district[1]["data"] == {"withheld": True}
    assert api.scope_events(feed, where, MH) == feed


# ---------------------------------------------------- nothing forgotten ---

# Every route that reads, and which side of the line it is on. A new read
# route fails this test until someone decides whether it returns rows about a
# centre (and guards it) or an aggregate anyone signed in may see.
ROWS = {
    "/map/facilities": "pins narrowed to the caller's scope",
    "/facilities": "the list narrowed to the caller's scope",
    "/facilities/{facility_id}": "refused outside scope",
    "/facilities/{facility_id}/usage": "refused outside scope",
    "/facilities/{facility_id}/bed-reports": "refused outside scope",
    "/facilities/{facility_id}/attendance": "refused outside scope",
    "/facilities/{facility_id}/trust": "refused outside scope",
    "/facilities/{facility_id}/workspace": "the centre and those who oversee it",
    "/facilities/{facility_id}/supply": "the centre and those who oversee it",
    "/facilities/{facility_id}/incoming": "the centre and those who oversee it",
    "/facilities/{facility_id}/bed-code": "the centre and those who oversee it",
    "/facilities/{facility_id}/handsets": "the centre itself",
    "/me/attendance": "the reader's own record",
    "/transfers": "trips narrowed to the caller's scope",
    "/transfers/oversight": "counts for all; named lists for the state's officer",
    "/movements": "totals for all; rows narrowed to the caller's scope",
    "/trust/queue": "refused to the national role; officers pinned to their patch",
    "/outbreaks/active": "district figures for all; named centres within scope",
    "/warnings/next": "district figures for all; the first centre's name within scope",
    "/events": "payloads about a centre withheld outside scope",
    "/calls": "the call log narrowed to the caller's scope",
}
AGGREGATE = {
    "/skus", "/map/districts", "/federation/inspector", "/federation/live",
    "/outbreaks", "/outbreaks/advice", "/outbreaks/ncdc-status", "/outbreaks/idsp-reports/latest",
}


def test_no_route_is_registered_twice():
    # A decorator left on the wrong function registers a second handler for
    # the same path, and the first one answers. It happened to /events.
    seen = [
        (method, r.path)
        for r in api.router.routes
        if isinstance(r, APIRoute)
        for method in r.methods
    ]
    assert sorted(seen) == sorted(set(seen))


def test_the_event_feeds_handler_is_the_one_that_reads_the_feed():
    (route,) = [r for r in api.router.routes if isinstance(r, APIRoute) and r.path == "/events"]
    assert route.endpoint is api.poll_events


def test_every_read_route_is_classified():
    reads = {
        r.path for r in api.router.routes if isinstance(r, APIRoute) and "GET" in r.methods
    }
    assert reads == set(ROWS) | AGGREGATE

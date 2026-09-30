"""Declaring an outbreak (fix list #41): who may, what is checked, and what it
sets off — a re-plan of exactly the medicines the disease drives, through the
same solver and the same approval gate (spec v3 §12.5)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import api, outbreak, redistribution
from app.auth import Principal
from app.config import settings
from app.models import OutbreakEvent

STATE_MH = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)
STATE_KA = Principal(5, "ka@health.example", "KA officer", "state_officer", "KA", None, None, None)


class Session:
    def __init__(self, rows=()):
        self.added: list = []
        self.rows = {r.id: r for r in rows}
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 77

    async def commit(self):
        self.commits += 1

    async def get(self, model, key):
        return self.rows.get(key)


@pytest.fixture
def wired(monkeypatch):
    planned: list = []
    recorded: list = []

    async def district_ids(session, state, district):
        return ["F1", "F2"] if (state, district) == ("MH", "Nashik") else []

    async def plan(session, state, sku=None):
        planned.append((state, sku))
        return redistribution.PlanResult(
            state=state, sku=sku, solver="greedy", generated_at=datetime.now(timezone.utc),
            transfer_ids=[], unmet=[], manual_review=[], replaced_ids=[],
        )

    async def view(session, row, now):
        return outbreak.OutbreakView(row=row, ids=["F1", "F2"], medicines=[], surges=[])

    async def warns(session, v, now):
        return []

    async def record(session, kind, payload, state_silo=None):
        recorded.append((kind, payload))

    async def listed(session, ids):
        return []

    monkeypatch.setattr(outbreak, "district_ids", district_ids)
    monkeypatch.setattr(outbreak, "evaluate", view)
    monkeypatch.setattr(outbreak, "warnings", warns)
    monkeypatch.setattr(redistribution, "generate_plan", plan)
    monkeypatch.setattr(redistribution, "list_transfers", listed)
    monkeypatch.setattr(api.events, "record", record)
    monkeypatch.setattr(api, "_open_outbreak", _none)
    return planned, recorded


async def _none(*_a, **_k):
    return None


def declare(user, **body):
    payload = api.DeclareOutbreakIn(**{"state": "MH", "district": "Nashik", "disease": "Cholera", **body})
    session = Session()
    return session, asyncio.run(api.declare_outbreak(payload, session=session, user=user))


def test_an_officer_of_another_state_is_refused(wired):
    with pytest.raises(HTTPException) as exc:
        declare(STATE_KA)
    assert exc.value.status_code == 403


def test_a_disease_the_medicine_map_does_not_cover_is_refused(wired):
    with pytest.raises(HTTPException) as exc:
        declare(STATE_MH, disease="Rabies")
    assert exc.value.status_code == 422


def test_a_district_with_no_centres_is_refused(wired):
    with pytest.raises(HTTPException) as exc:
        declare(STATE_MH, district="Nowhere")
    assert exc.value.status_code == 404


def test_an_expected_surge_beyond_the_limit_is_refused():
    with pytest.raises(ValidationError):
        api.DeclareOutbreakIn(
            state="MH", district="Nashik", disease="Cholera",
            surge_pct=settings.outbreak_max_surge_pct + 1,
        )


def test_declaring_records_the_outbreak_for_the_spec_ttl_and_replans_its_medicines(wired):
    planned, recorded = wired
    session, out = declare(STATE_MH, surge_pct=60)
    (row,) = [o for o in session.added if isinstance(o, OutbreakEvent)]
    assert (row.state_silo, row.district, row.disease_category) == ("MH", "Nashik", "Cholera")
    assert row.source == "officer" and float(row.surge_pct) == 60
    assert row.declared_by == "MH officer"
    assert row.expires_at - datetime.now(timezone.utc) > timedelta(days=settings.outbreak_ttl_days - 1)
    # One single-medicine plan per medicine the disease drives: the same
    # solver, and never the rate-limited all-medicine plan.
    assert planned == [("MH", "ORS"), ("MH", "IVFLUID"), ("MH", "ZINC")]
    assert recorded[0][0] == "outbreak.declared"
    assert out.outbreak.id == row.id


def test_ending_an_outbreak_replans_and_it_stops_counting(wired, monkeypatch):
    planned, _ = wired
    row = OutbreakEvent(
        id=5, state_silo="MH", district="Nashik", disease_category="Dengue", source="officer",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    session = Session([row])
    asyncio.run(api.end_outbreak(5, session=session, user=STATE_MH))
    assert row.ended_at is not None
    assert not outbreak.is_active(row, datetime.now(timezone.utc))
    assert planned == [("MH", "PARA500"), ("MH", "IVFLUID")]


def test_only_the_states_officers_may_end_it(wired):
    row = OutbreakEvent(
        id=5, state_silo="MH", district="Nashik", disease_category="Dengue", source="officer",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.end_outbreak(5, session=Session([row]), user=STATE_KA))
    assert exc.value.status_code == 403

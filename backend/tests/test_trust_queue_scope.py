"""The audit queue refuses a national scope instead of taking 46 seconds.

Scoring every facility in the country live measured 46.2s against the deployed
database (docs/STORAGE_NOTES.md), long enough for the panel's live refresh to
pile requests on top of each other. The queue is about where to send someone,
which is always a state or a district, so an unbounded request is refused
before any query runs.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app import api, trust
from app.auth import Principal


def _user(role: str, state: str | None = None, district: str | None = None) -> Principal:
    return Principal(
        id=1,
        email="officer@example.test",
        name="Officer",
        role=role,
        state_silo=state,
        district=district,
        facility_id=None,
        staff_ref=None,
    )


def _call(user: Principal, **query):
    return asyncio.run(
        api.audit_queue(
            state=query.get("state"),
            district=query.get("district"),
            limit=50,
            session=None,
            user=user,
        )
    )


def test_an_administrator_with_no_state_is_refused_before_any_query(monkeypatch):
    called = False

    async def spy(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(trust, "audit_queue", spy)
    with pytest.raises(HTTPException) as exc:
        _call(_user("admin"))
    assert exc.value.status_code == 400
    assert not called


@pytest.mark.parametrize(
    "user, query, expected",
    [
        (_user("admin"), {"state": "MH"}, ("MH", None)),
        (_user("admin"), {"state": "MH", "district": "Nashik"}, ("MH", "Nashik")),
        (_user("state_officer", "BR"), {}, ("BR", None)),
        (_user("block_mo", "MH", "Nashik"), {}, ("MH", "Nashik")),
    ],
)
def test_a_bounded_scope_still_reaches_the_scorer(monkeypatch, user, query, expected):
    seen = {}

    async def spy(session, *, state=None, district=None, limit=50):
        seen["scope"] = (state, district)
        return []

    monkeypatch.setattr(trust, "audit_queue", spy)
    assert _call(user, **query) == []
    assert seen["scope"] == expected

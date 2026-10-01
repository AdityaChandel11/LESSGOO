"""One trust number, one scale (fix list #89).

The live audit found one centre showing "0.58" on the pharmacist's card and
"64 out of 100" in the officer's drawer at the same moment. The drawer scored
the centre live; the card read the materialised copy the national map keeps.
Spec v3 §12.6: the score is computed live from the same tables everything else
reads, except one materialised copy for the national map. So every
single-centre view scores live, and every view says it on the same 0–100 scale.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app import api, services, trust


class NoSession:
    async def execute(self, *_a, **_k):
        raise AssertionError("the live path must not read the materialised copy")


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    async def fake(session, facility_ids=None, **kwargs):
        seen.update(kwargs, facility_ids=facility_ids)
        return []

    monkeypatch.setattr(services, "get_snapshots", fake)
    return seen


def test_the_officer_drawer_scores_the_centre_live(monkeypatch):
    seen = _capture(monkeypatch)

    async def readable(session, facility_id, user):
        return type("F", (), {"id": facility_id})()

    # Who may open the drawer is fix #77's rule, tested in test_governance_tiers.
    monkeypatch.setattr(api, "_readable_facility", readable)
    with pytest.raises(HTTPException):
        asyncio.run(api.get_facility("HFR-MH-PHC-00001", session=object(), user=None))
    assert seen["live_trust"] is True


def test_the_pharmacist_workspace_scores_the_centre_live(monkeypatch):
    seen = _capture(monkeypatch)

    async def own(session, facility_id, user):
        return type("F", (), {"id": facility_id})()

    monkeypatch.setattr(api, "_facility_in_scope", own)
    with pytest.raises(HTTPException):
        asyncio.run(api.facility_workspace("HFR-MH-PHC-00001", session=object(), user=None))
    assert seen["live_trust"] is True


def test_the_daily_briefing_ranks_by_the_same_live_statuses(monkeypatch):
    # The briefing sits on the same screen as the workspace rows; a status
    # widened by a different score would contradict the row beside it.
    seen = _capture(monkeypatch)

    async def own(session, facility_id, user):
        return type("F", (), {"id": facility_id})()

    monkeypatch.setattr(api, "_facility_in_scope", own)
    with pytest.raises(HTTPException):
        asyncio.run(api.facility_briefing("HFR-MH-PHC-00001", session=object(), user=None))
    assert seen["live_trust"] is True


def test_live_figures_come_from_trust_compute_for_those_centres_only(monkeypatch):
    asked: list = []

    async def compute(session, scope):
        asked.append(scope)
        return [trust.Score(facility_id="A", score=0.64, band="watch", components=[])]

    monkeypatch.setattr(trust, "compute", compute)
    figures = asyncio.run(services.trust_figures(NoSession(), ["A"], live=True))
    assert asked == [trust.Scope(facility_ids=("A",))]
    assert figures == {"A": services.TrustFigure(score=0.64, band="watch")}


def test_the_escalation_reason_uses_the_zero_to_hundred_scale():
    assert services.trust_reason(0.58, 1.17) == (
        "data confidence 58 out of 100, so this facility is warned 1.2x earlier"
    )


def test_no_reason_when_the_warning_is_not_widened():
    assert services.trust_reason(0.9, 1.0) is None
    assert services.trust_reason(None, 1.0) is None

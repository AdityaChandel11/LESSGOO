"""Fix #46: a "Why?" that tells a first-time reader something new.

The old answer restated the figures already on the trip card. The new one is
built from the solver's own data, after the fact: which nearer centre was not
used and why, when the receiver runs out if nobody sends, how fresh the
donor's figure is and how far the receiver's data can be relied on, and that
the distance is an estimate. Gemini words those facts in English and Hindi;
it may not add a figure. With no model, the facts themselves are the answer.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app import redistribution as R

RULES = R.PlanRules(
    trigger_days=7, target_days=14, donor_floor_days=14, road_factor=1.3, max_km=150,
    cold_chain_max_km=60, avg_speed_kmh=40, handling_hours=1, min_units=10, critical_cost_weight=1,
)


def node(fid, lat, qty, burn, *, name=None, district="Nanded"):
    days = qty / burn if burn else None
    status = "critical" if days is not None and days < 3 else "healthy"
    return R.StockNode(fid, name or fid, district, lat, 77.0, qty, burn, days, status)


RECEIVER = node("R", 19.00, 5, 10, name="Nanded PHC 9")           # half a day
DONOR = node("D", 19.30, 600, 10, name="Nanded PHC 3")            # 60 days, ~43 km away
NEAR_AT_FLOOR = node("N", 19.05, 90, 10, name="Nanded PHC 7")     # 9 days: under the 14-day floor
NEAR_SHORT = node("S", 19.04, 20, 10, name="Nanded PHC 5")        # 2 days: short itself
NEAR_RICH = node("X", 19.06, 900, 10, name="Nanded PHC 2")        # 90 days, nearer than the donor


def alt(*others, committed=None):
    return R.alternative_donor(
        RECEIVER, DONOR, [RECEIVER, DONOR, *others], RULES, committed or {},
    )


def test_a_nearer_centre_under_its_floor_is_named_with_the_reason():
    a = alt(NEAR_AT_FLOOR)
    assert a["name"] == "Nanded PHC 7" and a["reason"] == "below_floor"
    assert a["sentence"] == (
        "Nanded PHC 7 is closer (about 7 km) but holds 9 days of stock, below the "
        "14-day floor a donor must keep."
    )


def test_a_nearer_centre_that_is_short_itself_is_said_to_be_short():
    a = alt(NEAR_SHORT)
    assert a["reason"] == "short_itself"
    assert "is itself short" in a["sentence"] and "2 days" in a["sentence"]


def test_a_nearer_centre_whose_spare_stock_is_promised_elsewhere_says_so():
    # 90 days held, 14 kept: 760 spare, all of it already on other open trips.
    a = alt(NEAR_RICH, committed={"X": 760})
    assert a["reason"] == "committed"
    assert "already promised to other trips" in a["sentence"]


def test_a_nearer_centre_that_could_have_sent_is_not_explained_away():
    a = alt(NEAR_RICH)
    assert a["reason"] == "could_spare"
    assert a["sentence"] == (
        "Nanded PHC 2 is closer (about 9 km) and could also spare stock; the plan is solved for "
        "the whole state at once, and this trip is part of its cheapest overall answer."
    )


def test_the_nearest_of_several_is_the_one_named():
    assert alt(NEAR_AT_FLOOR, NEAR_SHORT)["name"] == "Nanded PHC 5"


def test_with_no_nearer_centre_the_donor_is_the_nearest_that_holds_it():
    assert alt() is None
    far = node("F", 20.50, 900, 10, name="Nanded PHC 30")
    assert alt(far) is None


NOW = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)


def facts(**kw):
    base = dict(
        sku_name="ORS", receiver=RECEIVER, donor=DONOR, alternative=None,
        receiver_runs_out_on=date(2026, 10, 2), today=NOW.date(),
        donor_counted_on=date(2026, 9, 28), donor_source="form",
        receiver_trust=(64, "watch"), route_source="straight_line_x1.3", road_factor=1.3,
    )
    base.update(kw)
    return R.trip_facts(**base)


def test_the_facts_say_what_happens_if_nobody_sends():
    assert "Without this delivery Nanded PHC 9 runs out of ORS around 2 Oct." in facts()
    late = facts(receiver_runs_out_on=date(2026, 9, 29))
    assert any("would already have run out around 29 Sept" in f and "count" in f for f in late)
    assert not any("runs out" in f for f in facts(receiver_runs_out_on=None))


def test_the_facts_say_why_this_donor():
    assert facts()[0] == "Nanded PHC 3 is the nearest centre that holds ORS above its floor."
    named = facts(alternative=alt(NEAR_AT_FLOOR))
    assert named[0].startswith("Nanded PHC 7 is closer (about 7 km) but holds 9 days")


def test_the_facts_say_how_fresh_the_numbers_are():
    lines = facts()
    assert "Nanded PHC 3's figure is its count of 28 Sept, entered on the stock form." in lines
    assert "Nanded PHC 9's data confidence is 64 out of 100 (watch)." in lines
    unscored = facts(receiver_trust=None)
    assert not any("data confidence" in f for f in unscored)


def test_the_facts_say_the_distance_is_an_estimate_only_when_it_is():
    assert "The distance is a straight-line estimate multiplied by 1.3, not a road route." in facts()
    assert not any("estimate" in f for f in facts(route_source="google_routes"))


# ------------------------------------------------------ the model's part ---

import asyncio
import json

import httpx
import pytest

from app import api, vision
from app.auth import Principal

ROWS = ["Donor: Nanded PHC 3, Nanded district", "ORS: send 140 sachet. Receiver has less than 1 day of stock now."]
FACTS = [
    "Nanded PHC 7 is closer (about 7 km) but holds 9 days of stock, below the 14-day floor a donor must keep.",
    "Without this delivery Nanded PHC 9 runs out of ORS around 2 Oct.",
]


def _live(monkeypatch):
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    monkeypatch.setattr(vision, "RETRY_BACKOFF_S", (0.0, 0.0))
    vision.clear_explanation_cache()


def _ask(answer: dict, seen: dict | None = None):
    def reply(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(request.content)
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}
        return httpx.Response(200, json=body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            return await vision.explain_transfer(rows=ROWS, facts=FACTS, client=client)

    return asyncio.run(go())


def test_the_model_is_given_the_facts_and_answers_in_two_languages(monkeypatch):
    _live(monkeypatch)
    seen: dict = {}
    out = _ask(
        {"text": "Nanded PHC 7 is closer, 7 km away, but holds only 9 days against the 14-day floor.",
         "hi": "Nanded PHC 7 पास है, 7 km, पर उसके पास केवल 9 दिन का स्टॉक है।"},
        seen,
    )
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert FACTS[0] in prompt and ROWS[1] in prompt and "Hindi" in prompt
    assert seen["body"]["generationConfig"]["responseSchema"]["required"] == ["text", "hi"]
    assert out.text.startswith("Nanded PHC 7 is closer") and out.hi.startswith("Nanded PHC 7 पास है")


def test_a_figure_added_in_either_language_discards_the_answer(monkeypatch):
    _live(monkeypatch)
    with pytest.raises(vision.VisionError, match="figure"):
        _ask({"text": "It will save 30 lives.", "hi": "ठीक है।"})
    vision.clear_explanation_cache()
    with pytest.raises(vision.VisionError, match="figure"):
        _ask({"text": "Nanded PHC 7 is closer.", "hi": "इससे 30 लोग बचेंगे।"})


def test_an_answer_in_one_language_only_is_discarded(monkeypatch):
    _live(monkeypatch)
    with pytest.raises(vision.VisionError, match="both"):
        _ask({"text": "Nanded PHC 7 is closer."})


OFFICER = Principal(1, "mh@example.org", "MH officer", "state_officer", "MH", None, None, None)


def _trip_row():
    return {
        "id": 1, "state": "MH", "sku_code": "ORS", "sku_name": "ORS", "unit": "sachet", "qty": 140.0,
        "route_km": 43.0, "route_source": "haversine",
        "rationale": {"recipient_days_before": 0.5, "recipient_days_after_this": 14.0, "donor_days_after_plan": 46.0},
        "from": {"id": "D", "name": "Nanded PHC 3", "district": "Nanded"},
        "to": {"id": "R", "name": "Nanded PHC 9", "district": "Nanded"},
    }


def test_with_no_model_the_facts_themselves_are_the_answer(monkeypatch):
    async def rows(session, *, ids=None, **_):
        return [_trip_row()]

    async def facts(session, items):
        return FACTS

    monkeypatch.setattr(R, "list_transfers", rows)
    monkeypatch.setattr(api, "_trip_facts", facts)
    out = asyncio.run(api.explain_trip(api.TripExplainIn(transfer_ids=[1]), session=None, user=OFFICER))
    assert out.ai is False and out.source == "rules"
    assert out.facts == FACTS and out.text_hi is None
    assert out.text.startswith("Nanded PHC 9 has less than 1 day of ORS")


def test_the_models_wording_is_returned_with_the_facts_it_was_given(monkeypatch):
    async def rows(session, *, ids=None, **_):
        return [_trip_row()]

    async def facts(session, items):
        return FACTS

    async def answer(*, rows, facts, client=None):
        return vision.Explanation(text="In words.", model="m", latency_ms=5, hi="शब्दों में।")

    monkeypatch.setattr(R, "list_transfers", rows)
    monkeypatch.setattr(api, "_trip_facts", facts)
    monkeypatch.setattr(vision, "explanation_available", lambda: True)
    monkeypatch.setattr(vision, "explain_transfer", answer)
    out = asyncio.run(api.explain_trip(api.TripExplainIn(transfer_ids=[1]), session=None, user=OFFICER))
    assert (out.ai, out.text, out.text_hi, out.facts) == (True, "In words.", "शब्दों में।", FACTS)


# ------------------------------------------- who planned it, said truthfully ---


def test_the_label_names_the_solver_that_actually_ran():
    by = lambda **r: R.planned_by([{"triggered_by": r.pop("triggered_by", "threshold"), "rationale": r}])  # noqa: E731
    assert by(solver="ortools") == "The plan is computed by OR-Tools."
    assert by(solver="greedy") == "The plan is computed by the greedy fallback solver."
    assert by(triggered_by="facility_request") == (
        "This is a request one centre raised to another, not a recommendation of the state plan."
    )
    assert by() == "The plan is computed by the redistribution solver."


def test_a_centres_own_request_is_not_explained_as_the_plans_choice():
    lines = facts(requested=True, alternative=alt(NEAR_RICH))
    assert lines[0] == "Nanded PHC 9 chose this donor itself, from the nearest centres that could spare ORS."
    assert not any("cheapest overall answer" in f for f in lines)

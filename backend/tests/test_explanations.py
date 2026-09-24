"""Plain-language "why" lines: grounded in the screen's figures, never an accusation.

No test here reaches Google. Live-mode tests inject an httpx MockTransport,
the same way the ward-photo tests do, and conftest blocks anything else.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import api, redistribution, trust, vision
from app.auth import Principal


@pytest.fixture(autouse=True)
def _fresh_cache():
    vision.clear_explanation_cache()
    yield
    vision.clear_explanation_cache()


def _live(monkeypatch) -> None:
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    monkeypatch.setattr(vision, "RETRY_BACKOFF_S", (0.0, 0.0))


def _answer(text: str):
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"text": text})}]}}]}
    return lambda request: httpx.Response(200, json=body)


def _trip(**rationale) -> dict:
    base = {
        "recipient_days_before": 0.62,
        "recipient_days_after_this": 14.0,
        "donor_days_after_plan": 24.3,
    }
    base.update(rationale)
    return {
        "id": 1,
        "sku_name": "ORS",
        "unit": "sachet",
        "qty": 414.0,
        "route_km": 2.4,
        "rationale": base,
        "from": {"id": "A", "name": "Nanded PHC 18", "district": "Nanded"},
        "to": {"id": "B", "name": "Nanded PHC 26", "district": "Nanded"},
    }


def _score(*components: trust.Component) -> trust.Score:
    return trust.Score(facility_id="F1", score=0.31, band="audit", components=list(components))


FLAGGED = trust._component(
    "attendance_vs_footfall", 0.9,
    "Staff recorded present on 35 of 56 shifts where no patients were logged at all.",
)
CLEAN = trust._component("receipt_discipline", 0.0, "Deliveries are confirmed promptly and in full.")


# ------------------------------------------------------------ grounding ---


def test_rounded_figures_from_the_inputs_are_allowed():
    rows = ["Receiver has 0.62 days of stock now, 14 days after this transfer."]
    assert vision.ungrounded_figures("It has 0.6 days, about 1, and gets 14.", rows) == []


def test_a_worked_out_percentage_is_not():
    rows = ["Staff recorded present on 35 of 56 shifts"]
    assert vision.ungrounded_figures("That is 62% of shifts.", rows) == ["62"]


def test_indian_digit_grouping_reads_as_one_number():
    assert vision.ungrounded_figures("send 1,234 tablets", ["send 1234 tablets"]) == []


def test_an_answer_with_an_invented_figure_is_refused():
    with pytest.raises(vision.VisionError, match="figure"):
        vision.parse_explanation(
            {"text": "It will last 30 days."}, model="m", sources=["14 days"], latency_ms=1
        )


@pytest.mark.parametrize("word", ["fraud", "Fraudulent", "stealing", "fake", "corruption"])
def test_trust_wording_that_accuses_is_refused(word):
    with pytest.raises(vision.VisionError, match="accusation"):
        vision.parse_explanation(
            {"text": f"This looks like {word}."},
            model="m", sources=[], latency_ms=1, forbid_accusation=True,
        )


def test_an_empty_answer_is_refused():
    with pytest.raises(vision.VisionError):
        vision.parse_explanation({"text": "  "}, model="m", sources=[], latency_ms=1)


# ------------------------------------------------------------- live path ---


def test_a_grounded_answer_comes_back_with_its_latency(monkeypatch):
    _live(monkeypatch)
    rows = redistribution.why_rows([_trip()], 3.0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_answer(
        "Nanded PHC 26 has less than 1 day of ORS left and this brings it to 14; "
        "Nanded PHC 18 still keeps 24.3 days."
    )))
    got = asyncio.run(vision.explain_transfer(rows=rows, client=client))
    assert got.text.startswith("Nanded PHC 26")
    assert got.latency_ms >= 0 and not got.cached


def test_the_same_question_is_never_paid_for_twice(monkeypatch):
    _live(monkeypatch)
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return _answer("Nanded PHC 26 is under 3 days and gets 14.")(request)

    rows = redistribution.why_rows([_trip()], 3.0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    first = asyncio.run(vision.explain_transfer(rows=rows, client=client))
    second = asyncio.run(vision.explain_transfer(rows=rows, client=client))
    assert len(calls) == 1
    assert second.cached and second.text == first.text


def test_a_trust_answer_that_accuses_falls_short(monkeypatch):
    _live(monkeypatch)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_answer("Staff are committing fraud.")))
    with pytest.raises(vision.VisionError, match="accusation"):
        asyncio.run(vision.explain_trust(
            facility_name="Nashik CHC 7", score=31, rows=trust.why_rows(_score(FLAGGED)),
            client=client,
        ))


# ---------------------------------------------------------- rules and rows ---


def test_trip_rows_carry_only_what_the_card_shows():
    rows = redistribution.why_rows([_trip()], 3.0)
    joined = "\n".join(rows)
    assert "less than 1 day of stock now" in joined
    assert "14 days after this transfer" in joined and "24.3 days of stock" in joined
    assert "Under 3 days" in joined


def test_under_a_day_is_never_written_as_zero():
    rows = redistribution.why_rows([_trip(recipient_days_before=0.02)], 3.0)
    assert "0 days" not in "\n".join(rows)


def test_the_rules_sentence_for_a_trip_names_the_worst_medicine():
    worse = _trip(recipient_days_before=0.2, recipient_days_after_this=5.0)
    worse["sku_name"] = "Zinc"
    text = redistribution.rules_why([_trip(), worse])
    assert text.startswith("Nanded PHC 26 has less than 1 day of Zinc")
    assert "2 medicines" in text


def test_trust_rows_leave_out_signals_that_agree():
    rows = trust.why_rows(_score(FLAGGED, CLEAN))
    assert len(rows) == 1 and "35 of 56" in rows[0]


def test_a_facility_whose_signals_agree_gets_no_model_call():
    assert trust.rules_why(_score(CLEAN)) == "Its signals agree with each other."


# --------------------------------------------------------------- endpoints ---


def _admin() -> Principal:
    return Principal(
        id=1, email="a@example.test", name="Admin", role="admin",
        state_silo=None, district=None, facility_id=None, staff_ref=None,
    )


def test_with_the_model_off_a_trip_gets_the_rules_line_unlabelled(monkeypatch):
    async def rows(session, *, ids=None, **_):
        return [_trip()]

    monkeypatch.setattr(redistribution, "list_transfers", rows)
    out = asyncio.run(api.explain_trip(api.TripExplainIn(transfer_ids=[1]), session=None))
    assert out.source == "rules" and out.ai is False and out.model is None
    assert out.text.startswith("Nanded PHC 26 has less than 1 day of ORS")


def test_transfers_from_two_trips_are_refused(monkeypatch):
    other = _trip()
    other["to"] = {"id": "C", "name": "Elsewhere", "district": "Pune"}

    async def rows(session, *, ids=None, **_):
        return [_trip(), other]

    monkeypatch.setattr(redistribution, "list_transfers", rows)
    with pytest.raises(api.HTTPException) as exc:
        asyncio.run(api.explain_trip(api.TripExplainIn(transfer_ids=[1, 2]), session=None))
    assert exc.value.status_code == 422


def test_a_model_failure_falls_back_and_says_why(monkeypatch):
    async def rows(session, *, ids=None, **_):
        return [_trip()]

    async def refuses(**_):
        raise vision.VisionError("the model's request quota for today is used up")

    monkeypatch.setattr(redistribution, "list_transfers", rows)
    monkeypatch.setattr(vision, "explanation_available", lambda: True)
    monkeypatch.setattr(vision, "explain_transfer", refuses)
    out = asyncio.run(api.explain_trip(api.TripExplainIn(transfer_ids=[1]), session=None))
    assert out.ai is False and "quota" in (out.note or "")

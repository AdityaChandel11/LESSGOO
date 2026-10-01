"""Fix #85: a daily briefing worth reading.

The Today card is a prioritised to-do computed from the centre's own rows —
recounts, orders, deliveries, requests, an outbreak nearby, the ward report,
the check-in. Gemini rewrites that list in English, Hindi and the state's
language; it may not add a line or a figure, and when it is unavailable the
computed list is what renders. No test here reaches Google.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest

from app import api, vision, workspace
from app.workspace import COUNT_OVERDUE, BriefingRow, TodoDelivery, TodoRequest


def row(name="ORS", qty=40.0, days=1.2, status="critical", unit="sachet", **kw) -> BriefingRow:
    return BriefingRow(sku_name=name, unit=unit, qty=qty, days_of_stock=days, status=status, **kw)


def todo(**kw) -> list[workspace.TodoItem]:
    base = dict(
        rows=[], deliveries=[], awaiting_your_reply=0, own_waiting=[],
        outbreaks=[], bed_report_today=None, checked_in_today=None,
    )
    base.update(kw)
    return workspace.todo_items(**base)


OVERDUE_ROW = row(
    "Amoxicillin 250mg", 300, 0.0, COUNT_OVERDUE, "capsule",
    last_counted_on=date(2026, 9, 24), ran_out_on=date(2026, 9, 27),
)
LATE = TodoDelivery("Zinc Sulphate 20mg", "tablet", 500, "WH-MH-NASHIK", date(2026, 9, 28), True)
COMING = TodoDelivery("ORS", "sachet", 200, "Nashik PHC 13", date(2026, 10, 3), False)
WAITING = TodoRequest("Paracetamol 500mg", "tablet", 519, "Nashik PHC 13")
OUTBREAK = "Cholera reported in Nashik district (declared by a state officer)"


# ------------------------------------------------------------ the list ---


def test_the_list_runs_from_most_urgent_to_least():
    items = todo(
        rows=[row("Iron Folic Acid", 900, 5.5, "at_risk", "tablet"), row(), OVERDUE_ROW],
        deliveries=[COMING, LATE],
        awaiting_your_reply=2,
        own_waiting=[WAITING],
        outbreaks=[OUTBREAK],
        bed_report_today=False,
        checked_in_today=0,
    )
    assert [i.kind for i in items][:7] == [
        "recount", "order", "delivery_overdue", "reply_needed", "outbreak",
        "order_soon", "delivery_arriving",
    ]


def test_each_line_says_what_to_do_and_names_its_tab():
    (recount,) = todo(rows=[OVERDUE_ROW])
    assert recount.en.startswith("Recount Amoxicillin 250mg")
    assert "24 Sept" in recount.en and "27 Sept" in recount.en
    assert recount.hi and recount.tab == "medicines"

    (late,) = todo(deliveries=[LATE])
    assert "500 tablet" in late.en and "WH-MH-NASHIK" in late.en and "28 Sept" in late.en
    assert late.tab == "orders"


def test_a_medicine_already_requested_is_not_ordered_twice():
    items = todo(rows=[row("Paracetamol 500mg", 10, 0.4, "critical", "tablet")], own_waiting=[WAITING])
    assert [i.kind for i in items] == ["request_waiting"]
    assert "519" in items[0].en and "Nashik PHC 13" in items[0].en


def test_requests_for_this_centres_stock_are_counted_not_listed():
    (item,) = todo(awaiting_your_reply=2)
    assert item.kind == "reply_needed" and "2 requests" in item.en and item.tab == "orders"
    (one,) = todo(awaiting_your_reply=1)
    assert "1 request " in one.en


def test_the_ward_report_and_check_in_appear_only_when_known_to_be_missing():
    assert [i.kind for i in todo(bed_report_today=False, checked_in_today=0)] == [
        "bed_report", "check_in",
    ]
    # Sent today, somebody checked in: nothing to chase.
    done = todo(rows=[row(days=31.0, status="healthy")], bed_report_today=True, checked_in_today=2)
    assert [i.kind for i in done] == ["all_clear"]
    # Unknown is not "missing".
    assert [i.kind for i in todo(rows=[row(days=31.0, status="healthy")])] == ["all_clear"]


def test_with_nothing_to_do_the_list_is_the_computed_line():
    rows = [row(days=31.0, status="healthy")]
    (item,) = todo(rows=rows)
    assert item.en == workspace.rules_briefing(rows)["en"]
    assert item.hi == workspace.rules_briefing(rows)["hi"]


def test_the_list_is_capped_and_keeps_the_most_urgent():
    many = [
        row(f"Medicine {n}", 300, 0.0, COUNT_OVERDUE, "tablet",
            last_counted_on=date(2026, 9, 24), ran_out_on=date(2026, 9, 27))
        for n in range(12)
    ]
    items = todo(rows=many, bed_report_today=False)
    assert len(items) == workspace.MAX_TODO
    assert all(i.kind == "recount" for i in items)


def test_the_fingerprint_follows_the_list():
    a = todo(rows=[row()])
    assert workspace.todo_hash(a) == workspace.todo_hash(todo(rows=[row()]))
    assert workspace.todo_hash(a) != workspace.todo_hash(todo(rows=[row()], deliveries=[LATE]))


def test_the_state_language_is_named_only_where_it_is_not_hindi():
    mr = workspace.state_language("MH")
    assert (mr.code, mr.name) == ("mr", "Marathi")
    assert workspace.state_language("KL").code == "ml"
    assert workspace.state_language("BR") is None
    assert workspace.state_language("UP") is None


# --------------------------------------------------------- the model ---

SOURCES = [
    "Order ORS today: 40 sachet left, about 1.2 days of cover.",
    "Delivery overdue: 500 tablet of Zinc Sulphate 20mg from WH-MH-NASHIK, expected 28 Sept.",
]


def test_the_models_lines_are_kept_per_language():
    out = vision.parse_briefing(
        {"en": ["Order ORS today — 40 sachets left.", "Chase the 500 zinc tablets due 28 Sept."],
         "hi": ["आज ORS मँगाएँ — 40 पैकेट बचे हैं।", "28 Sept को आने वाली 500 ज़िंक गोलियों का पता करें।"],
         "local": ["आज ORS मागवा — 40 पाकिटे उरली आहेत.", "500 झिंक गोळ्यांचा पाठपुरावा करा."]},
        model="m", sources=SOURCES, local="mr",
    )
    assert set(out.lines) == {"en", "hi", "mr"}
    assert out.lines["mr"][0].startswith("आज ORS")


def test_a_figure_the_centre_never_reported_discards_the_answer():
    with pytest.raises(vision.VisionError, match="figure"):
        vision.parse_briefing(
            {"en": ["Order ORS today — 40 sachets left, enough for 3 patients."], "hi": ["आज ORS मँगाएँ।"]},
            model="m", sources=SOURCES, local=None,
        )


def test_devanagari_digits_are_checked_like_any_other():
    with pytest.raises(vision.VisionError, match="figure"):
        vision.parse_briefing(
            {"en": ["Order ORS today."], "hi": ["आज ORS मँगाएँ — ९९ पैकेट।"]},
            model="m", sources=SOURCES, local=None,
        )


def test_the_model_may_not_add_a_line():
    with pytest.raises(vision.VisionError, match="more lines"):
        vision.parse_briefing(
            {"en": ["Order ORS.", "Chase zinc.", "Also tidy the store."], "hi": ["क", "ख", "ग"]},
            model="m", sources=SOURCES, local=None,
        )


def test_a_missing_state_language_discards_the_answer():
    with pytest.raises(vision.VisionError, match="Marathi|language"):
        vision.parse_briefing(
            {"en": ["Order ORS today."], "hi": ["आज ORS मँगाएँ।"]},
            model="m", sources=SOURCES, local="mr",
        )


def test_the_prompt_carries_the_list_and_names_the_language(monkeypatch):
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    monkeypatch.setattr(vision, "RETRY_BACKOFF_S", (0.0, 0.0))
    seen: dict = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        text = json.dumps({"en": ["Order ORS today."], "hi": ["आज ORS मँगाएँ।"], "local": ["आज ORS मागवा."]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            return await vision.write_briefing(
                facility_name="Nashik PHC 1", rows=SOURCES,
                local=("mr", "Marathi"), client=client,
            )

    out = asyncio.run(go())
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "Marathi" in prompt and SOURCES[0] in prompt and "Nashik PHC 1" in prompt
    assert "local" in seen["body"]["generationConfig"]["responseSchema"]["required"]
    assert out.lines["mr"] == ["आज ORS मागवा."]


# ------------------------------------------------------- the fallback ---


def test_without_the_model_the_computed_list_is_the_answer():
    items = todo(rows=[row()], deliveries=[LATE])
    en = api._rules_reply(items, "en", workspace.state_language("MH"), None)
    assert en.source == "rules" and en.ai is False and en.lines == [i.en for i in items]
    hi = api._rules_reply(items, "hi", workspace.state_language("MH"), None)
    assert hi.lines == [i.hi for i in items]


def test_the_state_language_falls_back_to_english_and_says_why():
    items = todo(rows=[row()])
    mr = api._rules_reply(items, "mr", workspace.state_language("MH"), None)
    assert mr.lines == [i.en for i in items] and mr.ai is False
    assert "Marathi" in mr.note and "Gemini" in mr.note


# ------------------------------------------------------------ the cache ---


def test_the_cache_accepts_every_language_the_list_can_be_written_in():
    # facility_briefings.lang was limited to en and hi; a Marathi list could
    # not be stored. The constraint is a two-letter code, and every state
    # language is one.
    import re
    from pathlib import Path

    from app.models import FacilityBriefing

    (check,) = [c for c in FacilityBriefing.__table_args__ if getattr(c, "name", "") == "ck_briefings_lang"]
    rule = str(check.sqltext)
    assert rule == "lang ~ '^[a-z]{2}$'"
    assert all(re.fullmatch("[a-z]{2}", l.code) for l in workspace.STATE_LANGUAGES.values())
    migrations = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    assert any(rule in p.read_text(encoding="utf-8") for p in migrations.glob("*.py"))

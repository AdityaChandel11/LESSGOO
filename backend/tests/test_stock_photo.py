"""A photographed document changes the shelf the way that document means it.

Fix list #11. Every line read from a bill, delivery slip or register page used
to be written as the shelf's new level, so a slip for 10 tablets turned a shelf
of 500 into 10 — and the forecast then learned 490 tablets of demand that never
happened. Now the document's type decides: a delivery slip adds, through the
delivery ledger, so it cannot be counted twice; an issue record subtracts; a
stock count sets. Anything doubtful is not applied, and says why — spec v3
§26.4: "Confidence below threshold routes to human confirmation instead of
writing silently."
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import stockphoto, vision, workspace
from app.stockphoto import OpenDelivery, SettledDelivery

TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)


def decide(**over) -> stockphoto.Decision:
    args = dict(
        document_type="stock_count",
        qty=120.0,
        printed_unit="tablets",
        printed_batch=None,
        sku_name="Paracetamol 500mg",
        sku_unit="tablet",
        on_hand=500.0,
        last_count_at=NOW - timedelta(days=2),
        document_date=TODAY,
        today=TODAY,
        confidence=0.9,
        open_deliveries=[],
        settled_deliveries=[],
        confidence_floor=0.6,
        max_age_days=7,
    )
    args.update(over)
    return stockphoto.decide_line(**args)


def held(d: stockphoto.Decision, *words: str) -> None:
    assert d.action == "hold", d
    assert d.qty_after is None
    for w in words:
        assert w.lower() in (d.reason or "").lower(), d.reason


# ------------------------------------------------ what each document does ---


def test_a_stock_count_sets_the_shelf():
    d = decide(document_type="stock_count", qty=120.0)
    assert (d.action, d.qty_after) == ("set", 120.0)


def test_an_issue_record_subtracts_what_was_issued():
    d = decide(document_type="issue_record", qty=40.0)
    assert (d.action, d.qty_after) == ("subtract", 460.0)


def test_a_delivery_slip_adds_to_the_shelf_instead_of_replacing_it():
    """The bug itself: a slip for 10 against a shelf of 500."""
    d = decide(
        document_type="delivery_slip", qty=10.0,
        open_deliveries=[OpenDelivery(movement_id=7, batch_id="TRF-7", qty=10.0)],
    )
    assert (d.action, d.movement_id, d.qty_after) == ("receive", 7, 510.0)


def test_a_slip_is_matched_by_its_printed_batch():
    d = decide(
        document_type="delivery_slip", qty=250.0, printed_batch=" wh-mh-0923 ",
        open_deliveries=[
            OpenDelivery(movement_id=3, batch_id="WH-MH-0917", qty=250.0),
            OpenDelivery(movement_id=4, batch_id="WH-MH-0923", qty=250.0),
        ],
    )
    assert (d.action, d.movement_id) == ("receive", 4)


def test_a_slip_whose_quantity_disagrees_with_its_dispatch_is_not_applied():
    d = decide(
        document_type="delivery_slip", qty=500.0, printed_batch="WH-MH-0923",
        open_deliveries=[OpenDelivery(movement_id=4, batch_id="WH-MH-0923", qty=450.0)],
    )
    held(d, "450", "Orders")


def test_a_slip_for_a_delivery_already_confirmed_is_not_added_again():
    d = decide(
        document_type="delivery_slip", qty=10.0, printed_batch="TRF-7",
        settled_deliveries=[SettledDelivery(batch_id="TRF-7", qty=10.0, received_at=NOW - timedelta(days=1))],
    )
    held(d, "Already confirmed on 28 Sept", "not added again")


def test_an_unbatched_slip_matching_a_confirmed_delivery_is_not_added_again():
    d = decide(
        document_type="delivery_slip", qty=10.0,
        settled_deliveries=[SettledDelivery(batch_id="TRF-7", qty=10.0, received_at=NOW - timedelta(days=3))],
    )
    held(d, "Already confirmed on 26 Sept")


def test_a_slip_with_no_dispatch_record_is_not_applied():
    held(decide(document_type="delivery_slip", qty=10.0), "no dispatch record")


def test_a_slip_matching_two_deliveries_is_left_to_the_orders_tab():
    d = decide(
        document_type="delivery_slip", qty=10.0,
        open_deliveries=[OpenDelivery(1, "A", 10.0), OpenDelivery(2, "B", 10.0)],
    )
    held(d, "Orders")


# ------------------------------------------------------ what is held back ---


def test_an_issue_that_would_go_below_zero_is_not_applied():
    held(decide(document_type="issue_record", qty=40.0, on_hand=25.0), "below zero")


def test_a_quantity_in_another_unit_is_not_applied():
    """Strips of ten are not tablets. The model is told never to convert."""
    held(decide(printed_unit="strips"), "strips", "tablet")


@pytest.mark.parametrize("printed,sku_unit", [
    ("Tabs.", "tablet"), ("TABLETS", "tablet"), ("capsules", "capsule"),
    ("Sachets", "sachet"), ("btl", "bottle"), ("amps", "ampoule"),
])
def test_the_same_unit_written_differently_is_accepted(printed, sku_unit):
    assert decide(printed_unit=printed, sku_unit=sku_unit).action == "set"


def test_a_line_with_no_printed_unit_is_applied():
    """Nothing to compare is not a mismatch."""
    assert decide(printed_unit=None).action == "set"


def test_a_document_older_than_the_limit_is_not_applied():
    held(decide(document_date=TODAY - timedelta(days=8)), "older than 7 days")


def test_a_count_dated_before_the_last_count_is_not_applied():
    """Applying it would put the shelf back to an older figure."""
    d = decide(document_date=TODAY - timedelta(days=3), last_count_at=NOW - timedelta(days=1))
    held(d, "before", "last count")


def test_an_issue_dated_before_the_last_count_is_not_applied():
    """The later count already has these issues in it."""
    d = decide(
        document_type="issue_record", qty=40.0,
        document_date=TODAY - timedelta(days=3), last_count_at=NOW - timedelta(days=1),
    )
    held(d, "before", "last count")


def test_a_document_dated_the_same_day_as_the_last_count_is_applied():
    assert decide(document_date=TODAY, last_count_at=NOW - timedelta(hours=2)).action == "set"


def test_a_slip_confirms_its_open_delivery_whatever_the_last_count():
    """Confirming an open delivery is what the Orders form does, and that form
    does not ask when the shelf was last counted either."""
    d = decide(
        document_type="delivery_slip", qty=10.0,
        document_date=TODAY - timedelta(days=3), last_count_at=NOW - timedelta(days=1),
        open_deliveries=[OpenDelivery(7, "TRF-7", 10.0)],
    )
    assert d.action == "receive"


def test_a_document_dated_in_the_future_is_not_applied():
    held(decide(document_date=TODAY + timedelta(days=3)), "future")


def test_a_document_dated_tomorrow_is_allowed_for_the_time_zone():
    """Dates are compared in UTC; just after midnight in India it is still
    yesterday in UTC, so a document dated today reads as tomorrow."""
    assert decide(document_date=TODAY + timedelta(days=1)).action == "set"


def test_a_document_the_reader_was_unsure_of_is_not_applied():
    held(decide(confidence=0.4), "unsure", "40%")


def test_a_document_of_unknown_type_is_not_applied():
    held(decide(document_type="unknown"), "delivery slip", "issue record", "stock count")


def test_a_document_with_no_date_is_still_read():
    assert decide(document_date=None).action == "set"


# ------------------------------------------------------ what the model says ---


def test_the_model_reports_the_document_type_and_each_line_unit_and_batch():
    out = vision.parse_stock_extraction(
        {"document_type": "delivery_slip",
         "lines": [{"medicine": "Paracetamol 500mg", "quantity": 10, "unit": "tablets", "batch": "TRF-7"}],
         "confidence": 0.9},
        model="t",
    )
    assert out.document_type == "delivery_slip"
    assert (out.lines[0].unit, out.lines[0].batch) == ("tablets", "TRF-7")


@pytest.mark.parametrize("raw", [None, "", "invoice", 7, "Delivery Slip!"])
def test_any_other_document_type_reads_as_unknown(raw):
    out = vision.parse_stock_extraction({"document_type": raw, "lines": [], "confidence": 0.9}, model="t")
    assert out.document_type == "unknown"


def test_the_prompt_asks_which_kind_of_document_it_is():
    for kind in ("delivery_slip", "issue_record", "stock_count"):
        assert kind in vision.STOCK_PROMPT
    assert "Do not convert units" in vision.STOCK_PROMPT


def test_the_mock_reader_says_it_read_a_stock_count():
    out = vision._mock_stock_extraction()
    assert (out.model, out.document_type) == ("mock", "stock_count")


# ------------------------------------------------- what the card says after ---


@pytest.mark.parametrize("doc,expected", [
    ({"document_type": "stock_count", "read_by": "gemini-3.5-flash-lite"}, "Stock count read by Gemini"),
    ({"document_type": "issue_record", "read_by": "gemini-3.5-flash-lite"}, "Issue record read by Gemini"),
    ({"document_type": "stock_count", "read_by": "mock"}, "Stock count read by the test reader (no model was called)"),
    ({"read_by": "gemini-3.5-flash-lite"}, "Read from a photo; the kind of document was not recorded"),
])
def test_a_photo_reading_says_which_document_it_came_from(doc, expected):
    p = workspace.provenance("photo", NOW, None, NOW, document=doc)
    assert (p.kind, p.detail) == ("photo", expected)


def test_a_photo_reading_is_never_called_counted_by_hand():
    assert workspace.provenance("photo", NOW, None, NOW).kind != "counted"

"""Every row says whether it is verified, and why, from its own records."""

from __future__ import annotations

from app import verification


def test_a_consignment_is_verified_only_when_both_records_agree():
    ok = verification.movement(
        status="received", qty_dispatched=500, qty_received=500, received_via="sms"
    )
    assert ok["verified"] and "500 of 500" in ok["reason"] and "by sms" in ok["reason"]

    short = verification.movement(status="short", qty_dispatched=500, qty_received=420)
    assert not short["verified"]
    assert "500 sent, 420 counted" in short["reason"] and "80 missing" in short["reason"]

    over = verification.movement(status="over", qty_dispatched=100, qty_received=112.5)
    assert not over["verified"] and "12.5 extra" in over["reason"]


def test_a_consignment_with_one_record_is_unverified_and_says_which_is_missing():
    waiting = verification.movement(status="in_transit", qty_dispatched=40, qty_received=None)
    assert not waiting["verified"] and "Only the dispatch record exists" in waiting["reason"]

    late = verification.movement(
        status="overdue", qty_dispatched=40, qty_received=None, days_outstanding=3.4
    )
    assert not late["verified"] and "3 days past the delivery window" in late["reason"]

    cancelled = verification.movement(status="cancelled", qty_dispatched=40, qty_received=None)
    assert not cancelled["verified"]


def test_a_transfer_is_verified_by_its_consignment_and_by_nothing_else():
    for status, word in (("proposed", "Awaiting"), ("rejected", "Declined"), ("cancelled", "Withdrawn")):
        v = verification.transfer(status=status, movement_row=None)
        assert not v["verified"] and word in v["reason"] and "dispatched" in v["reason"]

    # Accepted is a promise; the receiver's count is the proof.
    undispatched = verification.transfer(status="approved", movement_row=None)
    assert not undispatched["verified"] and "no dispatch record" in undispatched["reason"]

    on_the_road = verification.transfer(
        status="approved",
        movement_row={"status": "in_transit", "qty_dispatched": 60, "qty_received": None},
    )
    assert not on_the_road["verified"]

    arrived = verification.transfer(
        status="approved",
        movement_row={"status": "received", "qty_dispatched": 60, "qty_received": 60},
    )
    assert arrived["verified"] and "60 of 60" in arrived["reason"]


def test_an_audit_row_is_verified_when_every_signal_had_enough_observations():
    scored = {"signal": "receipt_discipline", "scored": True, "sample": 6}
    more = {"signal": "attendance_vs_footfall", "scored": True, "sample": 12}
    thin = {"signal": "beds_vs_register", "scored": False, "sample": 1}

    full = verification.trust_row([scored, more])
    assert full["verified"] and "All 2 signals" in full["reason"] and "18 observations" in full["reason"]

    partial = verification.trust_row([scored, more, thin])
    assert not partial["verified"]
    assert "1 of 3 signals" in partial["reason"] and "beds vs register" in partial["reason"]

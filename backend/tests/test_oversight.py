"""The Redistribution tab is oversight, not an approval queue (fix list #39).

Decision recorded in the fix list (spec v3 §1.6, "always ends in human
approval, nothing auto-executes"): the human approval is the donor centre's.
Officers and administrators watch the pipeline and act on exceptions; they do
not approve routine trips. The one labelled exception is the sandbox drill.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import redistribution
from app.auth import Principal, can_decide_transfer

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ADMIN = Principal(3, "admin@health.example", "Admin", "admin", None, None, None, None)
ROUTE = dict(from_state="MH", from_district="Pune", to_state="MH", to_district="Pune",
             from_facility="HFR-MH-PHC-00200")


def test_an_administrator_no_longer_approves_routine_trips():
    assert not can_decide_transfer(ADMIN, **ROUTE)


def _t(tid, status, *, created_days=0, trigger="solver_proposal", before=None, after=None, to="B"):
    return {
        "id": tid, "status": status, "triggered_by": trigger, "to": to,
        "created_at": NOW - timedelta(days=created_days),
        "recipient_days_before": before, "recipient_days_after_plan": after,
    }


def _m(mid, status, *, transfer_id, expected_in_days=1.0):
    return {"id": mid, "status": status, "transfer_id": transfer_id,
            "expected_by": NOW + timedelta(days=expected_in_days)}


def test_the_pipeline_counts_each_stage_from_the_rows():
    transfers = [
        _t(1, "proposed"), _t(2, "proposed"), _t(3, "approved"), _t(4, "approved"),
        _t(5, "approved"), _t(6, "rejected"), _t(7, "cancelled"),
    ]
    movements = [
        _m(10, "in_transit", transfer_id=3),
        _m(11, "received", transfer_id=4),
        _m(12, "short", transfer_id=5),
    ]
    assert redistribution.pipeline(transfers, movements) == {
        "recommended": 7, "awaiting_donor": 2, "accepted": 3, "declined": 1, "withdrawn": 1,
        "in_transit": 1, "received": 2, "verified": 1,
    }


def test_would_lift_counts_receivers_the_open_plan_takes_over_the_line():
    proposals = [
        _t(1, "proposed", to="A", before=1.0, after=4.0),
        _t(2, "proposed", to="A", before=1.0, after=4.0),
        _t(3, "proposed", to="B", before=1.0, after=2.0),
        _t(4, "proposed", to="C", before=5.0, after=9.0),
        _t(5, "proposed", to="D", before=2.0, after=3.5, trigger="facility_request"),
    ]
    assert redistribution.would_lift(proposals, critical_days=3.0) == 2


def test_no_reply_lists_proposals_older_than_the_reply_window():
    window = timedelta(hours=24)
    rows = [_t(1, "proposed", created_days=2), _t(2, "proposed", created_days=0.5),
            _t(3, "approved", created_days=5)]
    assert [r["id"] for r in redistribution.no_reply(rows, NOW, window)] == [1]


def test_not_received_lists_deliveries_past_their_expected_day():
    rows = [_m(1, "in_transit", transfer_id=1, expected_in_days=-1),
            _m(2, "in_transit", transfer_id=2, expected_in_days=1),
            _m(3, "received", transfer_id=3, expected_in_days=-3)]
    assert [r["id"] for r in redistribution.not_received(rows, NOW)] == [1]

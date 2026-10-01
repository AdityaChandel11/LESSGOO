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


# ------------------------------------------------- cross-district (fix #38) ---
# Checked by dry-running the solver on the seeded data: an ordinary plan stays
# inside a district wherever its own donors are enough (Maharashtra: 0 of 506
# trips), crosses districts where they are not (Bihar: 144 of 823), and an
# outbreak that doubles a district's demand pulls stock in from its neighbours
# (Pune -> Nashik). The count below is of trips, read from the open
# recommendations; nothing is staged to make it non-zero.


def test_cross_district_trips_are_counted_among_the_open_recommendations():
    district = {"A": "Nashik", "B": "Nashik", "C": "Pune", "D": "Pune"}
    transfers = [
        {"from": "A", "to": "B", "status": "proposed", "sku": "ORS"},       # inside Nashik
        {"from": "C", "to": "B", "status": "proposed", "sku": "ORS"},       # Pune -> Nashik
        {"from": "C", "to": "B", "status": "proposed", "sku": "ZINC"},      # the same trip, a second medicine
        {"from": "D", "to": "A", "status": "approved", "sku": "ORS"},       # already decided: not open
        {"from": "D", "to": "A", "status": "rejected", "sku": "ZINC"},
    ]
    assert redistribution.cross_district_trips(transfers, district) == 1


def test_a_centre_with_no_known_district_is_not_called_cross_district():
    transfers = [{"from": "X", "to": "B", "status": "proposed", "sku": "ORS"}]
    assert redistribution.cross_district_trips(transfers, {"B": "Nashik"}) == 0


# ------------------------------------------------ outcomes (fix #49) ---
# What actually happened to the trips that were accepted, from the ledger's
# own rows: what was sent against what the receiver counted, how long a
# recommendation took to arrive, and whether the centres that were critical
# are above the line now. No "stock-outs averted": that would be an estimate.


def _done(tid, to="B", sku="ORS", *, hours=30.0, before=1.0, sent=100.0, got=100.0):
    created = NOW - timedelta(days=3)
    transfer = {"id": tid, "status": "approved", "to": to, "sku": sku, "created_at": created,
                "recipient_days_before": before}
    movement = {"id": tid * 10, "transfer_id": tid, "to": to, "sku": sku, "batch": f"B{tid}",
                "status": "received" if got == sent else ("short" if got < sent else "over"),
                "qty": sent, "qty_received": got, "received_at": created + timedelta(hours=hours)}
    return transfer, movement


def _outcomes(pairs, critical_now=()):
    return redistribution.outcomes(
        [t for t, _ in pairs], [m for _, m in pairs], set(critical_now), critical_days=3.0
    )


def test_what_was_sent_is_set_against_what_the_receiver_counted():
    out = _outcomes([_done(1), _done(2, got=80.0), _done(3, got=110.0)])
    assert (out["received"], out["in_full"], out["short"], out["over"]) == (3, 1, 1, 1)
    assert out["units_short"] == 20.0
    (mismatch,) = out["short_deliveries"]
    assert (mismatch["transfer_id"], mismatch["sent"], mismatch["received"]) == (2, 100.0, 80.0)


def test_a_delivery_still_on_the_road_is_not_an_outcome_yet():
    t, m = _done(1)
    m = {**m, "status": "in_transit", "qty_received": None, "received_at": None}
    out = _outcomes([(t, m)])
    assert out["received"] == 0 and out["median_hours"] is None


def test_the_time_from_recommendation_to_receipt_is_the_median():
    out = _outcomes([_done(1, hours=10), _done(2, hours=30), _done(3, hours=200)])
    assert out["median_hours"] == 30.0


def test_centres_that_were_critical_are_checked_against_where_they_stand_now():
    pairs = [
        _done(1, to="A", before=1.0),   # was critical, is above the line now
        _done(2, to="B", before=1.0),   # was critical, still critical
        _done(3, to="C", before=5.0),   # was never critical
    ]
    out = _outcomes(pairs, critical_now=[("B", "ORS")])
    assert (out["were_critical"], out["lifted"]) == (2, 1)


def test_one_centre_helped_twice_is_counted_once():
    out = _outcomes([_done(1, to="A", before=1.0), _done(2, to="A", before=0.5)])
    assert (out["were_critical"], out["lifted"]) == (1, 1)

"""Re-running a plan must not destroy work.

Fix list #37. generate_plan deleted every proposed transfer in the state,
whoever raised it: a pharmacist's request for stock vanished whenever an
officer pressed "Re-run plan", and a donor looking at a recommendation found
it gone. The solver also ignored stock already promised to open requests, so
it could propose a donor's spare stock a second time — against spec v3
§12.3: "Never propose taking a donor below its own safety stock."
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import redistribution
from app.redistribution import Promise, StockNode

AT = datetime(2026, 9, 30, 8, 32, tzinfo=timezone.utc)


def node(fid: str, qty: float, burn: float = 10.0) -> StockNode:
    return StockNode(fid, fid, "Nashik", 0.0, 0.0, qty, burn, qty / burn, "healthy")


# ------------------------------------------------------ what may be replaced ---


def test_a_replan_replaces_the_solvers_own_proposals():
    assert redistribution.replaceable_on_replan("threshold")


def test_a_replan_never_touches_a_centres_own_request():
    assert not redistribution.replaceable_on_replan("facility_request")


def test_a_proposal_of_unknown_origin_is_kept():
    assert not redistribution.replaceable_on_replan(None)


# ------------------------------------------------- stock already promised ---


def test_a_donor_offers_only_what_is_not_already_promised():
    donor, other = node("D", 500.0), node("X", 40.0)
    out = redistribution.after_promises([donor, other], [Promise("D", "R", 300.0)])
    by_id = {n.facility_id: n for n in out}
    assert by_id["D"].qty == 200.0
    assert by_id["D"].days == 20.0
    assert by_id["X"].qty == 40.0


def test_a_centre_already_owed_stock_counts_it_as_coming():
    """A pending request to a centre is stock on its way; planning another
    delivery on top of it would help the same centre twice."""
    out = redistribution.after_promises([node("R", 10.0)], [Promise("D", "R", 50.0)])
    assert out[0].qty == 60.0


def test_promises_add_up_across_several_requests():
    out = redistribution.after_promises(
        [node("D", 500.0)], [Promise("D", "R1", 100.0), Promise("D", "R2", 150.0)]
    )
    assert out[0].qty == 250.0


def test_a_donor_is_never_counted_below_zero():
    out = redistribution.after_promises([node("D", 100.0)], [Promise("D", "R", 300.0)])
    assert out[0].qty == 0.0


def test_the_solver_keeps_a_promised_donor_above_its_floor():
    """End to end through the pure solver: 500 on hand, 300 promised, a
    14-day floor at 10 a day is 140 — so at most 60 more may be proposed."""
    rules = redistribution.PlanRules.from_settings()
    donor = node("D", 500.0)
    needy = StockNode("R", "R", "Nashik", 0.01, 0.01, 5.0, 10.0, 0.5, "critical")
    nodes = redistribution.after_promises([donor, needy], [Promise("D", "OTHER", 300.0)])
    plan = redistribution.plan_sku("ORS", nodes, rules, controlled=False, cold_chain=False)
    sent = sum(p.qty for p in plan.proposals if p.from_id == "D")
    assert sent <= 500.0 - 300.0 - rules.donor_floor_days * 10.0 + 1e-6


# ------------------------------------------------ the donor is told why ---


def test_a_repeated_recommendation_says_it_was_updated():
    old = {("D", "R", "ORS"): 41}
    rationale = redistribution.replacement_note(("D", "R", "ORS"), old, AT)
    assert rationale == {"replaces_transfer": 41, "updated_at": AT.isoformat()}


def test_a_new_recommendation_carries_no_update_note():
    assert redistribution.replacement_note(("D", "R2", "ORS"), {("D", "R", "ORS"): 41}, AT) == {}


def test_acting_on_a_replaced_recommendation_names_the_time_in_india():
    text = redistribution.replaced_message(AT)
    assert "14:02" in text  # 08:32 UTC is 14:02 in India
    assert "replaced" in text

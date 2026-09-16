"""Safety rules for the redistribution solver (spec 12.3).

These are the guarantees an officer relies on when they click Approve, so each
one is tested against both the OR-Tools solver and the greedy fallback.
"""

import pytest

from app.redistribution import PlanRules, StockNode, plan_sku, split_roles

RULES = PlanRules(
    trigger_days=7,
    target_days=14,
    donor_floor_days=14,
    road_factor=1.3,
    max_km=150,
    cold_chain_max_km=60,
    avg_speed_kmh=35,
    handling_hours=0.5,
    min_units=5,
    critical_cost_weight=0.35,
)

# 1 degree of latitude is ~111 km; with the 1.3 road factor, ~144 road km.
KM_PER_DEG_ROAD = 111.2 * 1.3
BASE_LAT, BASE_LNG = 19.0, 75.0


def node(fid: str, road_km: float, qty: float, burn: float) -> StockNode:
    """A facility `road_km` due north of the origin."""
    days = qty / burn if burn else None
    if days is None or days >= 7:
        status = "healthy"
    elif days >= 3:
        status = "at_risk"
    else:
        status = "critical"
    return StockNode(
        facility_id=fid, name=fid, district="Test", lat=BASE_LAT + road_km / KM_PER_DEG_ROAD,
        lng=BASE_LNG, qty=qty, burn=burn, days=days, status=status,
    )


SOLVERS = ["ortools", "greedy"]


@pytest.mark.parametrize("solver", SOLVERS)
def test_donor_is_never_taken_below_its_floor(solver):
    donor = node("donor", 0, qty=1000, burn=20)  # 50 days, can spare 720
    recipients = [node(f"r{i}", 10 + i, qty=0, burn=40) for i in range(4)]  # each needs 560

    plan = plan_sku("ORS", [donor, *recipients], RULES, solver=solver)

    given = sum(p.qty for p in plan.proposals if p.from_id == "donor")
    assert given > 0
    assert donor.qty - given >= RULES.donor_floor_days * donor.burn
    for p in plan.proposals:
        # The plan-wide figure is the binding one: it counts everything the
        # donor gives across all its transfers.
        assert p.rationale["donor_days_after_plan"] >= RULES.donor_floor_days


@pytest.mark.parametrize("solver", SOLVERS)
def test_controlled_substances_are_never_routed(solver):
    nodes = [node("donor", 0, qty=500, burn=2), node("short", 5, qty=1, burn=2)]

    plan = plan_sku("MORPH", nodes, RULES, controlled=True, solver=solver)

    assert plan.proposals == []
    assert plan.solver == "manual"
    assert [s.facility_id for s in plan.unmet] == ["short"]
    assert "manual review" in plan.unmet[0].reason.lower()


@pytest.mark.parametrize("solver", SOLVERS)
def test_cold_chain_items_stay_within_their_radius(solver):
    nodes = [node("donor", 0, qty=600, burn=10), node("short", 100, qty=10, burn=10)]

    cold = plan_sku("OXY", nodes, RULES, cold_chain=True, solver=solver)
    normal = plan_sku("OXY", nodes, RULES, cold_chain=False, solver=solver)

    assert cold.proposals == []
    assert cold.unmet and "60 km" in cold.unmet[0].reason
    assert len(normal.proposals) == 1


@pytest.mark.parametrize("solver", SOLVERS)
def test_nothing_travels_beyond_the_distance_limit(solver):
    nodes = [node("donor", 0, qty=600, burn=10), node("far", 200, qty=10, burn=10)]

    plan = plan_sku("ORS", nodes, RULES, solver=solver)

    assert plan.proposals == []
    assert plan.unmet[0].facility_id == "far"


@pytest.mark.parametrize("solver", SOLVERS)
def test_scarce_stock_reaches_the_critical_facility_first(solver):
    # The donor can spare 100. The critical facility is *further away* than the
    # at-risk one, and still gets served first — urgency outweighs distance.
    donor = node("donor", 0, qty=240, burn=10)
    critical = node("critical", 80, qty=10, burn=10)  # 1 day, needs 130
    at_risk = node("at_risk", 50, qty=50, burn=10)  # 5 days, needs 90

    plan = plan_sku("ORS", [donor, critical, at_risk], RULES, solver=solver)

    received = {p.to_id: p.qty for p in plan.proposals}
    assert received.get("critical") == 100
    assert "at_risk" not in received


@pytest.mark.parametrize("solver", SOLVERS)
def test_recipients_are_topped_up_to_target_and_no_further(solver):
    nodes = [node("donor", 0, qty=5000, burn=10), node("short", 20, qty=20, burn=10)]

    plan = plan_sku("ORS", nodes, RULES, solver=solver)

    assert len(plan.proposals) == 1
    p = plan.proposals[0]
    assert p.qty == 120  # 14 days x 10/day, minus the 20 on hand
    assert p.rationale["recipient_days_after_plan"] == pytest.approx(RULES.target_days)
    assert plan.unmet == []


@pytest.mark.parametrize("solver", SOLVERS)
def test_split_need_reports_single_and_combined_outcomes(solver):
    # Two donors, each able to spare 60; the recipient needs 140. Approving one
    # transfer alone must not claim the facility reaches its 14-day target.
    donors = [node("d1", 0, qty=200, burn=10), node("d2", 5, qty=200, burn=10)]
    short = node("short", 10, qty=0, burn=10)

    plan = plan_sku("ORS", [*donors, short], RULES, solver=solver)

    assert len(plan.proposals) == 2
    for p in plan.proposals:
        assert p.rationale["recipient_incoming_transfers"] == 2
        assert p.rationale["recipient_days_after_this"] == pytest.approx(p.qty / 10)
        assert p.rationale["recipient_days_after_plan"] == pytest.approx(12.0)
        assert p.rationale["recipient_days_after_this"] < p.rationale["recipient_days_after_plan"]


def test_facilities_without_measured_usage_are_left_out():
    nodes = [node("no_use_rich", 0, qty=900, burn=0), node("no_use_empty", 5, qty=0, burn=0)]

    donors, recipients = split_roles(nodes, RULES)

    assert donors == [] and recipients == []


@pytest.mark.parametrize("solver", SOLVERS)
def test_trivial_quantities_are_not_worth_a_trip(solver):
    # 6.8 days of cover (so it is a recipient), but reaching 14 days needs only
    # 4 units — below the 5-unit minimum.
    nodes = [node("donor", 0, qty=900, burn=1), node("nearly_fine", 5, qty=3.4, burn=0.5)]

    plan = plan_sku("ORS", nodes, RULES, solver=solver)

    assert plan.proposals == []


@pytest.mark.parametrize("solver", SOLVERS)
def test_unmet_need_is_reported_not_dropped(solver):
    donor = node("donor", 0, qty=160, burn=10)  # spares 20
    short = node("short", 10, qty=0, burn=10)  # needs 140

    plan = plan_sku("ORS", [donor, short], RULES, solver=solver)

    assert sum(p.qty for p in plan.proposals) == 20
    assert len(plan.unmet) == 1
    assert plan.unmet[0].units_needed == 120

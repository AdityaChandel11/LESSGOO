"""The pharmacist workspace's decisions, as pure functions.

Everything a screen in `frontend/src/workspace/` shows is decided here and
tested here, the same split `redistribution.py` already uses: `split_roles`
and `plan_sku` are pure and tested, `load_state_nodes` is the thin shim that
talks to the database and is covered by `checks/workspace.py` instead.

The rule running through all of it: a figure the system did not actually
verify is never described as verified, and a date it cannot compute is absent
rather than guessed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import workspace
from app.redistribution import PlanRules, StockNode

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)

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

PLAIN = workspace.SkuRule(code="ORS", name="Oral Rehydration Salts", unit="sachet",
                          is_controlled=False, cold_chain=False)
CONTROLLED = workspace.SkuRule(code="MORPH", name="Morphine Sulphate 10mg", unit="ampoule",
                               is_controlled=True, cold_chain=False)
COLD = workspace.SkuRule(code="OXY", name="Oxytocin Injection 5IU", unit="ampoule",
                         is_controlled=False, cold_chain=True)

# 1 degree of latitude is ~111 km; with the 1.3 road factor, ~144 road km.
KM_PER_DEG_ROAD = 111.2 * 1.3
BASE_LAT, BASE_LNG = 19.0, 75.0


def node(fid: str, road_km: float, qty: float, burn: float) -> StockNode:
    """A facility `road_km` due north of the origin, as test_redistribution does."""
    days = qty / burn if burn else None
    if days is None or days >= 7:
        status = "healthy"
    elif days >= 3:
        status = "at_risk"
    else:
        status = "critical"
    return StockNode(
        facility_id=fid, name=fid, district="Test",
        lat=BASE_LAT + road_km / KM_PER_DEG_ROAD, lng=BASE_LNG,
        qty=qty, burn=burn, days=days, status=status,
    )


def receipt(days_ago: float, batch: str = "TRF-412", qty: float = 400.0):
    return workspace.LastReceipt(
        batch_id=batch, qty_received=qty,
        received_at=NOW - timedelta(days=days_ago), received_via="form",
    )


# ========================================================== provenance ===
# "How was this number checked?" — the question the panel exists to answer.


def test_a_hand_count_is_reported_as_a_hand_count():
    p = workspace.provenance("form", NOW - timedelta(days=2), None, NOW)
    assert p.kind == "counted"
    assert p.days_ago == 2


def test_a_newer_delivery_outranks_an_older_count():
    p = workspace.provenance("form", NOW - timedelta(days=6), receipt(1), NOW)
    assert p.kind == "delivery"
    assert "TRF-412" in p.detail


def test_an_older_delivery_does_not_outrank_a_newer_count():
    p = workspace.provenance("form", NOW - timedelta(days=1), receipt(9), NOW)
    assert p.kind == "counted"


@pytest.mark.parametrize("source", ["sms", "ivr", "whatsapp"])
def test_a_phone_report_is_never_called_a_count(source):
    assert workspace.provenance(source, NOW, None, NOW).kind == "phone"


@pytest.mark.parametrize("source", ["form", "voice", "photo"])
def test_every_in_app_report_counts_as_a_count(source):
    assert workspace.provenance(source, NOW, None, NOW).kind == "counted"


@pytest.mark.parametrize("source", ["seed", "transfer"])
def test_a_row_no_person_reported_is_not_called_a_verification(source):
    """`seed` is synthetic baseline and `transfer` is stock moving on its own.
    Describing either as a count would invent a check that never happened."""
    p = workspace.provenance(source, NOW, None, NOW)
    assert p.kind == "system"
    assert "counted" not in p.detail.lower()


def test_nothing_reported_says_so_rather_than_guessing():
    p = workspace.provenance(None, None, None, NOW)
    assert p.kind == "none"
    assert p.at is None
    assert p.days_ago is None


def test_a_delivery_outranks_the_transfer_reading_it_caused():
    """Confirming a receipt writes a `transfer` stock reading at the same
    instant. A system row is not a verification, so it must not bury the
    delivery that produced it — the check caught exactly this."""
    at_the_same_moment = workspace.LastReceipt(
        batch_id="TRF-900", qty_received=460.0, received_at=NOW, received_via="form",
    )
    p = workspace.provenance("transfer", NOW, at_the_same_moment, NOW)
    assert p.kind == "delivery"
    assert "TRF-900" in p.detail


def test_a_real_count_still_outranks_an_older_delivery():
    """The rule above must not go the other way: a hand count made after the
    delivery is the newer evidence and keeps its place."""
    p = workspace.provenance("form", NOW, receipt(4), NOW)
    assert p.kind == "counted"


def test_a_delivery_alone_is_still_a_verification():
    p = workspace.provenance(None, None, receipt(3), NOW)
    assert p.kind == "delivery"
    assert p.days_ago == 3


# ======================================================= stockout date ===


def test_stockout_date_is_the_last_reading_plus_its_cover():
    at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert workspace.stockout_date(4.0, at) == datetime(2026, 9, 24).date()


def test_no_burn_rate_means_no_stockout_date():
    """Review Focus 1. An unknown rate must not become a date; absence of
    evidence must not manufacture an alert, the rule services.classify follows."""
    at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert workspace.stockout_date(None, at) is None


def test_a_cover_figure_with_nothing_to_count_from_gives_no_date():
    assert workspace.stockout_date(4.0, None) is None


def test_stock_already_out_dates_to_the_reading_itself():
    at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert workspace.stockout_date(0.0, at) == datetime(2026, 9, 20).date()


# ========================================================= find supply ===


def test_a_controlled_medicine_is_never_offered_a_donor():
    """Review Focus 3, and spec 12.3: controlled substances never enter the
    solver. MORPH sits on every facility and goes critical like anything else."""
    nodes = [node("A", 10, 900, 10), node("B", 20, 900, 10), node("ME", 0, 5, 10)]
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=CONTROLLED)
    assert s.donors == []
    assert s.manual_only is True
    assert "controlled" in s.reason.lower()


def test_donors_are_the_nearest_three_in_distance_order():
    nodes = [node("D0", 50, 900, 10), node("D1", 10, 900, 10), node("D2", 30, 900, 10),
             node("D3", 20, 900, 10), node("D4", 40, 900, 10), node("ME", 0, 5, 10)]
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN)
    assert [d.facility_id for d in s.donors] == ["D1", "D3", "D2"]


def test_no_donor_is_offered_below_its_own_floor():
    """Exactly 14 days of cover is the floor itself: there is nothing spare."""
    nodes = [node("TIGHT", 10, 140, 10), node("ME", 0, 5, 10)]
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN)
    assert s.donors == []


def test_a_donor_reports_the_cover_it_keeps():
    nodes = [node("D", 10, 900, 10), node("ME", 0, 5, 10)]
    d = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN).donors[0]
    assert d.spare_units == 760                       # 900 - 14 * 10
    assert d.days_kept == pytest.approx(14.0, abs=0.01)
    assert d.distance_basis == "straight_line_x1.3"


def test_the_asking_facility_is_never_its_own_donor():
    nodes = [node("ME", 0, 900, 10)]
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN)
    assert all(d.facility_id != "ME" for d in s.donors)


def test_a_cold_chain_medicine_respects_the_shorter_road_limit():
    nodes = [node("FAR", 100, 900, 10), node("ME", 0, 5, 10)]   # beyond 60 km
    assert workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=COLD).donors == []


def test_an_ordinary_medicine_reaches_further_than_a_cold_chain_one():
    nodes = [node("FAR", 100, 900, 10), node("ME", 0, 5, 10)]
    assert len(workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN).donors) == 1


def test_units_needed_is_what_the_target_asks_for():
    nodes = [node("D", 10, 900, 10), node("ME", 0, 5, 10)]      # 14 * 10 - 5
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN)
    assert s.units_needed == 135


def test_a_facility_that_is_not_short_is_told_so_rather_than_shown_donors():
    nodes = [node("D", 10, 900, 10), node("ME", 0, 900, 10)]
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=PLAIN)
    assert s.units_needed == 0
    assert s.donors == []
    assert "not short" in s.reason.lower()


# =============================================================== caps ===

LIMITS = workspace.CapLimits(per_facility=3, overall=100)


def test_a_request_is_allowed_below_both_caps():
    assert workspace.check_caps(2, 99, LIMITS).allowed is True


def test_the_fourth_request_for_a_facility_is_refused_by_name():
    v = workspace.check_caps(3, 10, LIMITS)
    assert v.allowed is False
    assert "3" in v.reason
    assert "this centre" in v.reason.lower()


def test_the_hundred_and_first_request_overall_is_refused_by_name():
    v = workspace.check_caps(0, 100, LIMITS)
    assert v.allowed is False
    assert "100" in v.reason
    assert "limit reached" in v.reason.lower()


def test_the_per_facility_limit_is_reported_before_the_global_one():
    """Both breached: tell the pharmacist about the queue they can act on."""
    assert "this centre" in workspace.check_caps(3, 100, LIMITS).reason.lower()


# ======================================================= request rules ===


def test_a_request_that_would_breach_the_donor_floor_is_refused_at_creation():
    """Review Focus 5. decide_transfer makes this check at approval; making it
    here too means the pharmacist hears it now, not after an officer opens it."""
    with pytest.raises(workspace.RequestRefused) as e:
        workspace.validate_request(node("D", 10, 200, 10), 100, sku=PLAIN, rules=RULES)
    assert "floor" in str(e.value).lower()
    assert "140" in str(e.value)


def test_a_request_that_leaves_exactly_the_floor_is_allowed():
    workspace.validate_request(node("D", 10, 200, 10), 60, sku=PLAIN, rules=RULES)


def test_a_controlled_medicine_cannot_be_requested():
    with pytest.raises(workspace.RequestRefused):
        workspace.validate_request(node("D", 10, 900, 10), 10, sku=CONTROLLED, rules=RULES)


def test_a_request_below_the_minimum_transfer_is_refused():
    with pytest.raises(workspace.RequestRefused):
        workspace.validate_request(node("D", 10, 900, 10), 2, sku=PLAIN, rules=RULES)


def test_a_donor_with_no_measured_usage_cannot_be_asked():
    """Without a burn rate there is no floor to respect, so there is no safe
    quantity to take — the same reason split_roles skips these facilities."""
    with pytest.raises(workspace.RequestRefused):
        workspace.validate_request(node("D", 10, 900, 0), 50, sku=PLAIN, rules=RULES)


# ================================================== delivery estimate ===

IST = timezone(timedelta(hours=5, minutes=30))


def test_the_estimate_shows_every_constant_it_used():
    est = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    assert est.assumptions == {
        "avg_speed_kmh": 35.0,
        "handling_hours": 0.5,
        "road_factor": 1.3,
        "dispatch_cutoff_hour": 14,
        "working_hours_per_day": 8.0,
    }
    assert est.basis == "straight_line_x1.3"
    assert est.label == "estimate"


def test_a_request_raised_after_the_cutoff_leaves_the_next_day():
    early = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    late = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 16, 0, tzinfo=IST))
    assert late.expected_on > early.expected_on


def test_a_longer_road_never_arrives_earlier():
    near = workspace.delivery_estimate(km=20.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    far = workspace.delivery_estimate(km=400.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    assert far.expected_on >= near.expected_on


def test_the_estimate_is_never_presented_as_a_promise():
    est = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    assert est.label == "estimate"
    assert est.basis != "google_routes"


# ========================================================== reference ===


def test_a_reference_number_is_stable_and_readable():
    assert workspace.reference(412) == "SS-000412"
    assert workspace.reference(1) == "SS-000001"

# ========================================================== briefing ===
# The line every pharmacist sees on load. It is deterministic, it needs no
# key, and it is what the screen falls back to when the model is unavailable
# or out of quota — so it is written and tested before the model exists.


def brief(**kw):
    return workspace.BriefingRow(
        sku_name=kw.get("sku_name", "Oral Rehydration Salts"),
        unit=kw.get("unit", "sachet"),
        qty=kw.get("qty", 120.0),
        days_of_stock=kw.get("days_of_stock", 2.1),
        status=kw.get("status", "critical"),
    )


def test_a_critical_medicine_is_named_with_its_own_figure():
    out = workspace.rules_briefing([brief()])
    assert "Oral Rehydration Salts" in out["en"]
    assert "2.1" in out["en"]
    assert out["hi"]


def test_the_worst_medicine_leads_not_the_first_one():
    out = workspace.rules_briefing([
        brief(sku_name="Paracetamol 500mg", days_of_stock=6.0, status="at_risk"),
        brief(sku_name="Oral Rehydration Salts", days_of_stock=1.2, status="critical"),
    ])
    assert "Oral Rehydration Salts" in out["en"]
    assert "Paracetamol" not in out["en"]


def test_at_risk_is_advised_less_urgently_than_critical():
    urgent = workspace.rules_briefing([brief(days_of_stock=1.0, status="critical")])
    soon = workspace.rules_briefing([brief(days_of_stock=6.0, status="at_risk")])
    assert urgent["en"] != soon["en"]
    assert "today" in urgent["en"].lower()


def test_a_healthy_centre_is_told_there_is_nothing_to_order():
    out = workspace.rules_briefing([brief(days_of_stock=31.0, status="healthy")])
    assert "nothing" in out["en"].lower() or "no medicine" in out["en"].lower()
    assert out["hi"]


def test_no_data_reads_differently_from_a_healthy_centre():
    """The dangerous confusion is "nothing to order" versus "nothing reported".
    A centre that has told us nothing must not read like a centre that is fine."""
    silent = workspace.rules_briefing([])
    healthy = workspace.rules_briefing([brief(days_of_stock=31.0, status="healthy")])
    assert silent["en"] != healthy["en"]
    assert silent["hi"] != healthy["hi"]
    assert "report" in silent["en"].lower()


@pytest.mark.parametrize("status", ["healthy", "at_risk", "critical"])
def test_a_medicine_with_no_cover_figure_is_never_given_one(status):
    """No Python value may leak into a numeric slot. The words are allowed to
    say "none"; the output is not allowed to print a None where a day count
    belongs, in either language."""
    out = workspace.rules_briefing([brief(days_of_stock=None, status=status)])
    for text in out.values():
        assert "None days" not in text
        assert "None " not in text.replace("None of", "")
        assert "nan" not in text.lower()
        assert "inf" not in text.lower()


def test_both_languages_are_always_present_and_different():
    for rows in ([], [brief()], [brief(days_of_stock=40.0, status="healthy")]):
        out = workspace.rules_briefing(rows)
        assert set(out) == {"en", "hi"}
        assert out["en"] and out["hi"]
        assert out["en"] != out["hi"]


# ---- the cache key -------------------------------------------------------
# A briefing is cached for a day. It must expire when the position it
# describes changes, or a pharmacist acts on yesterday's advice.


def test_the_same_position_hashes_the_same():
    rows = [brief(), brief(sku_name="Amoxicillin 250mg", days_of_stock=6.0)]
    assert workspace.briefing_hash(rows) == workspace.briefing_hash(list(rows))


def test_a_changed_days_figure_changes_the_hash():
    before = workspace.briefing_hash([brief(days_of_stock=6.0)])
    after = workspace.briefing_hash([brief(days_of_stock=2.0)])
    assert before != after


def test_reordering_the_same_medicines_does_not_change_the_hash():
    a = brief(sku_name="A", days_of_stock=3.0)
    b = brief(sku_name="B", days_of_stock=9.0)
    assert workspace.briefing_hash([a, b]) == workspace.briefing_hash([b, a])


def test_a_trivial_movement_does_not_invalidate_the_cache():
    """Rounded to a tenth of a day on purpose: a burn rate that drifts in the
    sixth decimal must not spend a model call every time someone opens the tab."""
    a = workspace.briefing_hash([brief(days_of_stock=6.00001)])
    b = workspace.briefing_hash([brief(days_of_stock=6.00002)])
    assert a == b

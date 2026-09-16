"""Cross-signal confidence scoring — spec 12.6."""

import pytest

from app import attendance, services, trust
from app.models import Facility


def test_bands_split_where_the_spec_says_they_do():
    assert trust.band_for(0.9) == trust.GOOD
    assert trust.band_for(0.75) == trust.GOOD
    assert trust.band_for(0.74) == trust.WATCH
    assert trust.band_for(0.5) == trust.WATCH
    assert trust.band_for(0.49) == trust.AUDIT


def test_weights_cover_the_whole_score():
    assert sum(trust.WEIGHTS.values()) == pytest.approx(1.0)


def test_a_components_cost_is_its_penalty_times_its_weight():
    c = trust._component("receipt_discipline", 0.5, "half the consignments are open")
    assert c.contribution == pytest.approx(0.5 * trust.WEIGHTS["receipt_discipline"])


def test_penalties_cannot_escape_their_range():
    assert trust._component("receipt_discipline", 5.0, "").penalty == 1.0
    assert trust._component("receipt_discipline", -2.0, "").penalty == 0.0


def test_low_confidence_warns_earlier_never_later():
    # A facility whose numbers cannot be trusted is more dangerous, not less.
    assert trust.warning_multiplier(1.0) == 1.0
    assert trust.warning_multiplier(0.4) == pytest.approx(1.35)
    assert trust.warning_multiplier(0.0) == pytest.approx(1.75)
    # An unscored facility is treated exactly as it was before the trust layer.
    assert trust.warning_multiplier(None) == 1.0


def test_a_facility_in_the_good_band_keeps_the_ordinary_thresholds():
    # Otherwise the screen contradicts itself: a facility labelled consistent
    # while being warned early.
    assert trust.band_for(0.8) == trust.GOOD
    assert trust.warning_multiplier(0.8) == 1.0
    assert trust.warning_multiplier(0.75) == 1.0
    # And the widening starts exactly where the band does, without a jump.
    assert trust.warning_multiplier(0.74) == pytest.approx(1.01)


def test_a_widened_threshold_trips_sooner_on_the_same_stock():
    # 9 days of cover: comfortable at full confidence, a warning at 0.4.
    assert services.classify(9.0) == services.STATUS_HEALTHY
    assert services.classify(9.0, trust.warning_multiplier(0.4)) == services.STATUS_AT_RISK
    # And the quantity itself is unchanged — only the moment someone is told.
    assert services.classify(2.0, 1.0) == services.STATUS_CRITICAL
    assert services.classify(2.0, 1.6) == services.STATUS_CRITICAL


def test_an_unknown_burn_rate_still_reads_as_healthy_at_any_confidence():
    assert services.classify(None, 2.0) == services.STATUS_HEALTHY


# ------------------------------------------------------- attendance rules ---


def facility() -> Facility:
    return Facility(
        id="HFR-MH-PHC-00001", name="Nashik PHC 1", type="PHC",
        state_silo="MH", district="Nashik", lat=19.9975, lng=73.7898, beds_total=6,
    )


def test_gps_inside_the_compound_clears_the_geofence():
    km, ok = attendance.resolve_location(
        facility(), lat=19.9976, lng=73.7899, loc_method="gps"
    )
    assert ok is True and km < 0.05


def test_gps_from_the_next_town_does_not():
    km, ok = attendance.resolve_location(
        facility(), lat=19.8467, lng=73.9976, loc_method="gps"
    )
    assert ok is False and km > 20


def test_a_cell_tower_gets_the_radius_a_cell_tower_deserves():
    _, ok = attendance.resolve_location(
        facility(), lat=20.005, lng=73.80, loc_method="cell_id"
    )
    assert ok is True


def test_a_channel_with_no_location_reports_that_it_ran_no_check():
    # An inbound call carries the caller, not the tower. Saying "unverified"
    # is the honest answer; inventing a pass would be the one lie this layer
    # cannot afford.
    km, ok = attendance.resolve_location(
        facility(), lat=None, lng=None, loc_method="none"
    )
    assert km is None and ok is None

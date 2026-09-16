"""Outbreak pre-positioning — spec 12.5.

The rule under test: a declaration raises expected demand for the commodities
that disease actually consumes, scaled by severity, and touches nothing else.
"""

import pytest

from app import outbreak


def test_the_commodity_table_is_readable_by_a_clinician():
    # Every entry is a disease mapped to medicines that disease consumes, with
    # a multiplier above 1. If this ever stops being legible at a glance, it
    # has stopped being reviewable by the people qualified to review it.
    for category, commodities in outbreak.OUTBREAK_COMMODITY_MAP.items():
        assert category in outbreak.CATEGORY_LABELS
        assert commodities, f"{category} lists no commodities"
        assert all(m > 1.0 for m in commodities.values())


def test_severity_scales_between_normal_and_the_clinical_multiplier():
    # Diarrhoeal disease at full severity triples ORS.
    assert outbreak.multiplier_for("acute_diarrheal_disease", "ORS", 1.0) == 3.0
    # A watchful declaration is the same mechanism, turned down.
    assert outbreak.multiplier_for("acute_diarrheal_disease", "ORS", 0.5) == 2.0
    # And zero severity changes nothing at all.
    assert outbreak.multiplier_for("acute_diarrheal_disease", "ORS", 0.0) == 1.0


def test_a_medicine_the_disease_does_not_consume_is_untouched():
    # Dengue does not raise demand for TB drugs, and pretending otherwise would
    # move stock away from facilities that need it.
    assert outbreak.multiplier_for("dengue_suspected", "TBDOTS", 1.0) == 1.0
    assert outbreak.multiplier_for("dengue_suspected", "ORS", 1.0) == 1.0


def test_an_unknown_disease_raises_nothing():
    assert outbreak.multiplier_for("not_a_disease", "ORS", 1.0) == 1.0


@pytest.mark.parametrize("severity", [-5.0, 2.0])
def test_severity_outside_its_range_cannot_amplify_beyond_the_table(severity):
    value = outbreak.multiplier_for("acute_diarrheal_disease", "ORS", severity)
    assert 1.0 <= value <= 3.0


def test_warning_gained_is_the_days_a_declaration_buys():
    # 12 days of cover at the old rate, 4 under the declared demand: the
    # facility becomes visible as at risk 8 days sooner.
    assert outbreak.warning_gained(12.0, 4.0) == 8.0


def test_warning_gained_is_never_negative():
    # A declaration cannot make a facility safer; if the arithmetic ever says
    # so, report no gain rather than a negative one.
    assert outbreak.warning_gained(4.0, 12.0) == 0.0


def test_warning_gained_is_unknown_when_cover_is_unknown():
    assert outbreak.warning_gained(None, 4.0) is None
    assert outbreak.warning_gained(12.0, None) is None


def test_an_enormous_starting_cover_does_not_claim_an_enormous_gain():
    # A facility with two years of stock is not "700 days of warning gained";
    # the claim is capped at the window the declaration actually covers.
    gain = outbreak.warning_gained(700.0, 5.0, ttl_days=14)
    assert gain == 23.0

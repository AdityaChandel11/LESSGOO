"""Fix #1: the Federation tab's figures, computed once on the server.

The panel used to multiply one model's size by the number of rounds and call
it "across the whole run", ignoring that four states upload every round; it
called round 0's error "round one"; and its headline compared the best round,
not the model that was kept. Every figure the panel prints now comes from
this summary of the recorded rounds, and nothing is typed into the screen.
"""

from __future__ import annotations

import pytest

from app import api

SILOS = {
    "BR": {"windows": 33918, "counts_as": 13194, "trust": 0.389, "flagged_pct": 37.1},
    "KL": {"windows": 15624, "counts_as": 13484, "trust": 0.863, "flagged_pct": 7.2},
    "MH": {"windows": 35547, "counts_as": 30000, "trust": 0.844, "flagged_pct": 8.0},
    "UP": {"windows": 54338, "counts_as": 45000, "trust": 0.828, "flagged_pct": 9.0},
}
# The recorded run's shape: round 0 is the untrained model, scored by the
# aggregator; eight training rounds follow.
MAES = [1.0674, 0.1623, 0.1154, 0.1140, 0.1131, 0.1124, 0.1117, 0.1110, 0.1106]


def rounds(maes=MAES, silos=4):
    return [
        {"round_no": i, "mae": m, "baseline": 0.1494, "silos": silos if i else None,
         "bytes": 22788, "per_silo": SILOS}
        for i, m in enumerate(maes)
    ]


def test_round_zero_is_the_untrained_model_not_round_one():
    s = api.federation_summary(rounds())
    assert s["untrained_mae"] == 1.0674
    assert s["final_mae"] == 0.1106 and s["final_round"] == 8


def test_the_headline_compares_the_final_model_with_the_burn_rate():
    s = api.federation_summary(rounds())
    assert s["final_improvement_pct"] == pytest.approx(26.0, abs=0.05)


def test_it_says_from_which_round_the_model_beats_the_burn_rate():
    assert api.federation_summary(rounds())["beats_baseline_from_round"] == 2
    never = api.federation_summary(rounds(maes=[1.0, 0.9, 0.8]))
    assert never["beats_baseline_from_round"] is None and never["final_improvement_pct"] < 0


def test_the_upload_total_counts_every_state_every_training_round():
    s = api.federation_summary(rounds())
    # 8 training rounds x 4 states x 22,788 bytes; nobody uploads round 0.
    assert s["training_rounds"] == 8 and s["silos"] == 4
    assert s["upload_bytes_total"] == 8 * 4 * 22788 == 729216


def test_the_examples_that_stayed_are_the_silos_own_windows():
    assert api.federation_summary(rounds())["total_windows"] == 139427


def test_an_empty_run_has_no_figures():
    s = api.federation_summary([])
    assert s["final_mae"] is None and s["upload_bytes_total"] == 0 and s["total_windows"] == 0

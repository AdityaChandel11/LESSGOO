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


# ------------------------------------------- the run against today's ledger ---
# Fix #54. The recorded run is dated 20 Sept; the demo data was reloaded on
# 21 Sept, and the reload re-drew which state has the weak paperwork. The run's
# per-state weights therefore describe a dataset that is no longer the one in
# the database. The inspector now computes each state's receipt discipline
# from today's ledger, by the federation's own rule, and says when the two
# differ — so the tab is honest whichever dataset is loaded.


def test_receipt_discipline_is_the_federations_own_rule():
    # Mirrors federation/pytorchexample/silo.py: an unconfirmed consignment
    # counts in full, a short one half, and 40% of that is a score of zero.
    trust, flagged = api.receipt_trust(total=100, overdue=10, short=10)
    assert (trust, flagged) == (0.625, 20.0)
    assert api.receipt_trust(total=0, overdue=0, short=0) == (1.0, 0.0)
    assert api.receipt_trust(total=10, overdue=10, short=0)[0] == 0.0


def test_the_rule_matches_the_federation_source():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "federation" / "pytorchexample" / "silo.py").read_text(
        encoding="utf-8"
    )
    assert "penalty = min(1.0, (overdue + 0.5 * short) / total / 0.4)" in src
    assert "interval '60 days'" in src


def test_a_run_whose_weights_no_longer_match_the_ledger_is_said_to_differ():
    recorded = {"BR": 0.389, "KL": 0.863, "MH": 0.755, "UP": 0.750}
    today = {"BR": 0.665, "KL": 0.797, "MH": 0.341, "UP": 0.664}
    assert api.silo_drift(recorded, today) == ["BR", "MH"]
    assert api.silo_drift(recorded, {**recorded, "KL": 0.84}) == []
    # A state today's ledger says nothing about is not called a difference.
    assert api.silo_drift(recorded, {"BR": 0.389}) == []

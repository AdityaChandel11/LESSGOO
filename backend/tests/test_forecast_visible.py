"""Make the forecast visible (fix list #84).

A centre's medicine gets a small chart: the last 28 days' use, the burn rate,
and — where the shared model covers that state and medicine — the model's
forecast for the next 7 days, with its version and age. Where the model never
saw the state or the medicine, the card says so instead of implying a
forecast exists.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import workspace

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
FEDERATION = Path(__file__).resolve().parents[1] / "federation" / "pytorchexample"


def _day(n: int, qty: float, source: str = "seed") -> tuple:
    return (NOW - timedelta(days=n), qty, source)


def test_daily_use_is_each_decline_on_the_day_it_was_counted():
    series = [_day(3, 100), _day(2, 90), _day(1, 85), _day(0, 85)]
    days = workspace.daily_use(series, NOW, window=4)
    assert [d.day for d in days] == [
        date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)
    ]
    assert [d.used for d in days] == [None, 10, 5, 0]


def test_a_restock_is_not_negative_use_and_a_transfer_out_is_not_use():
    series = [_day(3, 100), _day(2, 300), _day(1, 250, "transfer"), _day(0, 240)]
    days = workspace.daily_use(series, NOW, window=4)
    assert [d.used for d in days] == [None, 0, None, 10]


def test_a_decline_across_a_gap_is_spread_over_the_days_between_the_counts():
    # Two days between counts: 20 used is 10 a day, flagged as spread, the
    # same arithmetic the burn rate does (it divides by elapsed days).
    series = [_day(3, 100), _day(1, 80)]
    days = workspace.daily_use(series, NOW, window=4)
    assert [d.used for d in days] == [None, 10, 10, None]
    assert [d.spread for d in days] == [False, True, True, False]


def test_the_model_coverage_matches_the_federation_code():
    task = (FEDERATION / "task.py").read_text(encoding="utf-8")
    silo = (FEDERATION / "silo.py").read_text(encoding="utf-8")
    states = re.search(r"^SILOS = \[(.*?)\]", task, re.M).group(1)
    skus = re.search(r"^SKUS = \((.*?)\)", silo, re.M).group(1)
    assert workspace.MODEL_STATES == tuple(re.findall(r'"(\w+)"', states))
    assert workspace.MODEL_SKUS == tuple(re.findall(r'"(\w+)"', skus))


def test_a_fresh_forecast_is_named_with_its_version_and_day():
    note = workspace.forecast_note(
        in_model=True, fresh=True, published=datetime(2026, 9, 29, tzinfo=timezone.utc),
        version="fedprox-20260929-0600",
    )
    assert note == (
        "Next 7 days from the shared model, trained across 4 states (MH, KL, BR, UP) without "
        "moving their rows · fedprox-20260929-0600, published 29 Sept"
    )


def test_a_stale_forecast_says_the_burn_rate_is_used():
    note = workspace.forecast_note(
        in_model=True, fresh=False, published=datetime(2026, 9, 20, tzinfo=timezone.utc),
        version="fedprox-20260920-1200",
    )
    assert "older than 8 days" in note and "burn rate is used" in note


def test_outside_the_model_the_card_says_so():
    note = workspace.forecast_note(in_model=False, fresh=False, published=None, version=None)
    assert note == "Burn rate — this state or medicine is not in the shared model yet."

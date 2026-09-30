"""Outbreak → surge → warning → plan, computed (fix list #41).

Spec v3 §12.5: "Outbreak pre-positioning — a multiplier into 12.3, not a new
subsystem … same solver, no new code … Its output still funnels through the
same human-approval gate." The fix list adds where the multiplier may come
from: the district's own observed 14-day rise where the readings show one,
otherwise the declaring officer's expected surge, labelled an assumption. The
random "demand up N% (simulated)" is gone.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import idsp, outbreak, redistribution
from app.auth import Principal
from app.config import settings
from app.redistribution import PlanRules, StockNode

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------ where the rise comes from


def test_an_observed_rise_is_used_when_the_readings_show_one():
    s = outbreak.surge_from(observed=1.23, surge_pct=None)
    assert s == (1.23, outbreak.OBSERVED)


def test_the_observed_rise_wins_over_the_officers_assumption():
    assert outbreak.surge_from(observed=1.3, surge_pct=50) == (1.3, outbreak.OBSERVED)


def test_below_the_minimum_rise_the_officers_expected_surge_is_used_and_labelled():
    small = 1 + settings.outbreak_min_rise / 2
    assert outbreak.surge_from(observed=small, surge_pct=50) == (1.5, outbreak.ASSUMPTION)


def test_with_neither_nothing_is_multiplied():
    assert outbreak.surge_from(observed=None, surge_pct=None) is None
    assert outbreak.surge_from(observed=0.8, surge_pct=None) is None
    assert outbreak.surge_from(observed=None, surge_pct=0) is None


def _series(daily_use: float, days: int, start_qty: float = 5000.0) -> list[tuple]:
    """One reading a day, falling by `daily_use`, ending now."""
    return [
        (NOW - timedelta(days=days - i), start_qty - daily_use * i, "seed")
        for i in range(days + 1)
    ]


def test_the_observed_ratio_is_the_last_14_days_against_the_14_before():
    # 10/day for the first 14 days, 15/day for the last 14 → 1.5.
    first = [(NOW - timedelta(days=28 - i), 5000 - 10 * i, "seed") for i in range(15)]
    second = [(NOW - timedelta(days=14 - i), 5000 - 140 - 15 * i, "seed") for i in range(1, 15)]
    ratio = outbreak.consumption_ratio({"A": first + second}, NOW)
    assert ratio == pytest.approx(1.5, rel=0.02)


def test_no_ratio_without_use_in_the_earlier_window():
    flat = [(NOW - timedelta(days=28 - i), 100.0, "seed") for i in range(29)]
    assert outbreak.consumption_ratio({"A": flat}, NOW) is None


def test_the_medicines_an_outbreak_drives_come_from_the_disease_map():
    assert outbreak.medicines_for("Cholera") == ["ORS", "IVFLUID", "ZINC"]
    assert outbreak.medicines_for("Something unmapped") == []


# ------------------------------------------------------------- the solver


def _node(fid: str, qty: float, burn: float, district="Nashik") -> StockNode:
    return StockNode(fid, fid, district, 20.0, 73.8, qty, burn, qty / burn, "healthy")


def test_a_surge_turns_a_comfortable_centre_into_a_recipient():
    rules = PlanRules.from_settings()
    node = _node("A", qty=100, burn=10)  # 10 days: above the trigger
    donors, recipients = redistribution.split_roles([node], rules)
    assert recipients == []
    surged = outbreak.apply_surges({"ORS": [node]}, {("A", "ORS"): 2.0})["ORS"][0]
    assert surged.burn == 20 and surged.days == 5
    donors, recipients = redistribution.split_roles([surged], rules)
    assert [r.facility_id for r, _ in recipients] == ["A"]


def test_centres_outside_the_outbreak_are_untouched():
    a, b = _node("A", 100, 10), _node("B", 100, 10, district="Pune")
    out = outbreak.apply_surges({"ORS": [a, b]}, {("A", "ORS"): 2.0})["ORS"]
    assert out[1] == b


# ------------------------------------------------------------ the warning


def test_the_warning_gives_both_run_out_dates():
    counted = NOW - timedelta(days=1)
    row = outbreak.warning_for(
        qty=190, burn=10, last_reported_at=counted, multiplier=2.0, now=NOW
    )
    assert row == (date(2026, 10, 9), date(2026, 10, 19))


def test_a_run_out_already_past_at_the_surge_rate_is_a_count_not_a_date():
    # Counted 5 days ago with 3 days' cover at the surge rate: the shelf may
    # be empty now, and a date in the past on a warning reads as a mistake.
    assert outbreak.warning_for(
        qty=60, burn=10, last_reported_at=NOW - timedelta(days=5), multiplier=2.0, now=NOW
    ) == outbreak.OVERDUE


def test_no_warning_when_the_surge_run_out_is_beyond_the_horizon():
    assert outbreak.warning_for(
        qty=10_000, burn=10, last_reported_at=NOW, multiplier=1.5, now=NOW
    ) is None


def test_the_warning_line_reads_like_the_fix_list():
    line = outbreak.warning_line("Nashik", "ORS", date(2026, 10, 4), date(2026, 10, 19))
    assert line == "Nashik · ORS · runs out 4 Oct (19 Oct without the outbreak)"


# -------------------------------------------------------- who may declare

STATE_MH = Principal(4, "mh@health.example", "MH officer", "state_officer", "MH", None, None, None)
STATE_KA = Principal(5, "ka@health.example", "KA officer", "state_officer", "KA", None, None, None)
DEMO_MH = Principal(2, "maharashtra@demo.swasthsetu.in", "MH demo", "state_officer", "MH", None, None, None)


def test_a_state_officer_declares_in_their_own_state_only(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert outbreak.may_declare(STATE_MH, "MH", "Pune") is None
    assert outbreak.may_declare(STATE_KA, "MH", "Pune") is not None


def test_a_public_demo_account_declares_only_in_the_sandbox(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert outbreak.may_declare(DEMO_MH, "MH", settings.demo_sandbox_district) is None
    assert outbreak.may_declare(DEMO_MH, "MH", "Pune") is not None


def test_an_outbreak_lasts_the_spec_ttl():
    # v3 §12.5: set_temporary_forecast(..., ttl_days=14)
    assert settings.outbreak_ttl_days == 14


# ------------------------------------------------------- the RNG is gone


def test_stocking_advice_carries_no_simulated_percentage():
    for a in idsp.stocking_advice(None, 8):
        assert "demand_rise_pct" not in a
        assert not any("simulated" in s for s in a["signals"])


def test_no_random_generator_left_in_the_outbreak_code():
    for name in ("idsp.py", "outbreak.py"):
        source = (Path(__file__).resolve().parents[1] / "app" / name).read_text(encoding="utf-8")
        assert "import random" not in source and "random." not in source, name

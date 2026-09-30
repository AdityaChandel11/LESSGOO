"""As-of-now cover on the PHC medicine and Today cards (fix list #26).

Stored days of cover are measured at the last count, and the clock never moved
them: on 29 Sept a card read "Zinc 5.4 days … At risk … runs out about
29 Sept". The rule now: cover = stored cover − days since the count, floor 0,
reclassified; past the run-out date the medicine is "Count overdue" and the
card says when it was last counted and when it would have run out.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app import api, services, workspace
from app.workspace import COUNT_OVERDUE, BriefingRow

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


def test_cover_counts_down_from_the_count_and_is_reclassified():
    counted = NOW - timedelta(days=3)
    cover = workspace.cover_now(5.4, counted, "at_risk", NOW)
    assert round(cover.days_of_stock, 6) == 2.4
    assert cover.status == services.classify(2.4) == "critical"
    assert cover.count_overdue is False


def test_the_warning_multiplier_still_widens_the_threshold():
    counted = NOW - timedelta(days=1.5)
    # 5.5 left: at risk plainly, critical under a 2x-widened threshold (6 days).
    assert workspace.cover_now(7.0, counted, "healthy", NOW).status == "at_risk"
    assert workspace.cover_now(7.0, counted, "healthy", NOW, 2.0).status == "critical"


def test_past_the_run_out_date_the_count_is_overdue():
    counted = NOW - timedelta(days=5)
    cover = workspace.cover_now(3.1, counted, "at_risk", NOW)
    assert cover.days_of_stock == 0.0
    assert cover.status == COUNT_OVERDUE
    assert cover.count_overdue is True


def test_no_measured_use_means_no_countdown():
    cover = workspace.cover_now(None, NOW - timedelta(days=9), "healthy", NOW)
    assert cover.days_of_stock is None
    assert cover.status == "healthy"
    assert cover.count_overdue is False


def test_a_count_in_the_future_is_not_counted_backwards():
    cover = workspace.cover_now(4.0, NOW + timedelta(hours=2), "at_risk", NOW)
    assert cover.days_of_stock == 4.0


# ------------------------------------------------------------ Today card ---


def _row(name, qty, days, status, counted=None, ran_out=None):
    return BriefingRow(
        sku_name=name, unit="capsules", qty=qty, days_of_stock=days, status=status,
        last_counted_on=counted, ran_out_on=ran_out,
    )


def test_the_today_card_asks_for_a_count_when_a_shelf_is_overdue():
    line = workspace.rules_briefing([
        _row("Amoxicillin", 300, 0.0, COUNT_OVERDUE, date(2026, 9, 24), date(2026, 9, 27)),
        _row("ORS", 900, 20.0, "healthy"),
    ])
    assert line["en"] == (
        "Count the Amoxicillin shelf today — last counted 300 capsules on 24 Sept; "
        "at your usual use it would have run out around 27 Sept."
    )
    assert "24 Sept" in line["hi"] and "27 Sept" in line["hi"]


def test_the_today_card_names_how_many_other_shelves_need_counting():
    line = workspace.rules_briefing([
        _row("Amoxicillin", 300, 0.0, COUNT_OVERDUE, date(2026, 9, 24), date(2026, 9, 27)),
        _row("Zinc", 40, 0.0, COUNT_OVERDUE, date(2026, 9, 24), date(2026, 9, 28)),
    ])
    assert line["en"].endswith("1 other medicine is also overdue for a count.")


def test_the_model_is_told_the_count_is_overdue_not_zero_days_of_cover():
    lines = workspace.briefing_rows_from_skus([
        _row("Amoxicillin", 300, 0.0, COUNT_OVERDUE, date(2026, 9, 24), date(2026, 9, 27)),
    ])
    assert lines == [
        "- Amoxicillin: last counted 300 capsules on 24 Sept, count overdue "
        "(would have run out around 27 Sept at usual use)"
    ]


def test_briefing_rows_carry_the_as_of_now_position():
    stock = services.SkuStock(
        sku_code="AMOX", sku_name="Amoxicillin", qty_on_hand=300.0, daily_burn_rate=96.8,
        days_of_stock=3.1, rate_source="burn_rate", status="at_risk", is_controlled=False,
        cold_chain=False, last_reported_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc),
        last_source="form", last_confidence=None,
    )
    (row,) = api._briefing_rows([stock], {"AMOX": "capsules"}, NOW, 1.0)
    assert row.status == COUNT_OVERDUE
    assert row.last_counted_on == date(2026, 9, 24)
    assert row.ran_out_on == date(2026, 9, 27)

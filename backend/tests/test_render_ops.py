"""The hand-run Render operations: roll-forward (#30) and reading repairs
(#11r, #79).

These are run by hand, never automatically, through `scripts.remote`. What is asserted
here is what makes them safe to hand over: a dry run unless --confirm, a second
run that finds nothing to do, and every statement bounded by the district's
facility ids or by date — never a read or write of the whole country.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from scripts import repair_readings, roll_forward

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)


# ------------------------------------------------------------ roll-forward ---


def test_the_gap_is_whole_days_since_the_last_seeded_reading():
    assert roll_forward.gap_days(NOW - timedelta(days=10, hours=3), NOW) == 10
    assert roll_forward.gap_days(NOW - timedelta(hours=20), NOW) == 0
    assert roll_forward.gap_days(NOW + timedelta(hours=1), NOW) == 0


def test_the_plan_copies_the_last_g_days_and_trims_the_oldest_g_days():
    cutoff = NOW - timedelta(days=10, hours=3)
    oldest = cutoff - timedelta(days=120)
    plan = roll_forward.make_plan(cutoff=cutoff, oldest=oldest, now=NOW)
    assert plan.days == 10
    assert plan.copy_from == cutoff - timedelta(days=10)
    assert plan.trim_before == oldest + timedelta(days=10)
    assert plan.shift == timedelta(days=10)


def test_a_second_run_the_same_day_has_nothing_to_do():
    cutoff = NOW - timedelta(hours=2)
    plan = roll_forward.make_plan(cutoff=cutoff, oldest=cutoff - timedelta(days=120), now=NOW)
    assert plan.days == 0


def test_a_gap_longer_than_the_history_is_refused():
    cutoff = NOW - timedelta(days=40)
    with pytest.raises(roll_forward.RollForwardError):
        roll_forward.make_plan(cutoff=cutoff, oldest=cutoff - timedelta(days=30), now=NOW)


@pytest.mark.parametrize("op", roll_forward.OPERATIONS, ids=lambda o: o.label)
def test_every_operation_is_bounded_by_the_district(op):
    assert ("facility_id = ANY(:ids)" in op.where) or ("to_facility = ANY(:ids)" in op.where)
    assert op.sql().count(":ids") >= 1


def test_only_seeded_readings_are_copied_and_trimmed():
    by_label = {op.label: op for op in roll_forward.OPERATIONS}
    for label in ("copy the last days forward", "trim the oldest days"):
        where = by_label[label].where
        assert "source = 'seed'" in where
    assert "superseded_by IS NULL" in by_label["copy the last days forward"].where
    # A reading another row points at is never deleted from under it.
    assert "superseded_by = r.id" in by_label["trim the oldest days"].where


def test_only_rows_from_before_the_cutoff_move_so_nothing_lands_in_the_future():
    for op in roll_forward.OPERATIONS:
        if op.kind == "update":
            assert ":cutoff" in op.where or ":cutoff_date" in op.where, op.label


class DrySession:
    """Answers counts; any write is a test failure."""

    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        verb = sql.lstrip().split()[0].upper()
        assert verb == "SELECT", f"dry run issued {verb}"

        class R:
            def scalar(self_inner):
                return 0

            def all(self_inner):
                return []

            def one(self_inner):
                return (NOW - timedelta(days=10, hours=3), NOW - timedelta(days=130))

        return R()

    async def commit(self):
        raise AssertionError("dry run committed")


def test_without_confirm_nothing_is_written():
    session = DrySession()
    asyncio.run(roll_forward.run(session, ["F1", "F2"], confirm=False, now=NOW))
    assert session.statements  # it did survey


# ---------------------------------------------------------------- repairs ---


def test_the_bill_repair_is_bounded_to_one_centre_and_one_medicine():
    where = repair_readings.REPAIRS["bill"].where
    assert "f.name = :facility_name" in where
    assert "r.sku_code = 'PARA500'" in where
    assert "r.superseded_by IS NULL" in where


def test_the_test_report_repair_is_bounded_by_date_and_skips_the_sandbox():
    where = repair_readings.REPAIRS["test-reports"].where
    assert "r.reported_at >= :since" in where
    assert "r.reporter_ref LIKE 'test\\_%'" in where
    assert "NOT (f.state_silo = :sandbox_state AND f.district = :sandbox_district)" in where
    assert "r.superseded_by IS NULL" in where


def test_the_bill_repair_writes_only_when_exactly_one_reading_matches():
    assert repair_readings.may_write("bill", 1)
    assert not repair_readings.may_write("bill", 0)
    assert not repair_readings.may_write("bill", 2)
    assert repair_readings.may_write("test-reports", 3)
    assert not repair_readings.may_write("test-reports", 0)


def test_a_superseded_reading_points_at_the_one_that_now_stands():
    sql = repair_readings.SUPERSEDE_SQL
    assert "superseded_by = COALESCE(" in sql
    assert "p.reported_at < r.reported_at" in sql
    assert "p.superseded_by IS NULL" in sql
    assert "WHERE r.id = ANY(:ids)" in sql

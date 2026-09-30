"""Roll the demo district's history forward to today — fix list #30.

Run by hand, before judging, never on a schedule:

    python -m scripts.remote --confirm -- -m scripts.roll_forward             # dry run
    python -m scripts.remote --confirm -- -m scripts.roll_forward --confirm   # write

(`scripts.remote --confirm` only lets the child reach the deployed database;
this script writes nothing without its own --confirm.)

The seed's history ends on the day it ran. Every day after that, the demo
district's cards count down to "count overdue", its ward counts go stale and
its check-ins leave "today's team". This moves the district's clock to today:

  stock readings    the last G seeded days are copied G days forward and the
                    oldest G seeded days are deleted, in the same transaction.
                    Row count is flat, and only G days of rows are written —
                    shifting every reading would rewrite the whole history on
                    a disk with little room (CLAUDE.md, size guard).
  ward reports, check-ins, re-verification pings, warehouse dispatches and
  verification codes
                    small tables: their seed-era rows are shifted G days later.

G is the whole number of days since the district's latest seeded reading, so
a second run on the same day has nothing to do. Only rows from before that
cutoff move, so nothing lands in the future and rows a demo wrote since stay
where they happened. Every statement is bounded by the district's facility
ids. Afterwards the map's stored rows and the trust copy are rebuilt for the
district only, from the same functions the app uses.

Ask Aditya for Render's dashboard % before and after, and record both.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

STATE = "MH"
DISTRICT = "Nashik"
SEED_SOURCE = "seed"
# Codes are parked this far ahead while they move, so no two rows ever claim
# the same (facility, day) key half-way through the shift.
PARK_DAYS = 100_000
GAUGE_MB = 1024.0
# CLAUDE.md: dashboard ≈ measured × 1.29.
GAUGE_FACTOR = 1.29


class RollForwardError(Exception):
    """The district cannot be rolled forward this way."""


@dataclass(frozen=True)
class Plan:
    cutoff: datetime
    oldest: datetime
    days: int

    @property
    def shift(self) -> timedelta:
        return timedelta(days=self.days)

    @property
    def copy_from(self) -> datetime:
        return self.cutoff - self.shift

    @property
    def trim_before(self) -> datetime:
        return self.oldest + self.shift


def gap_days(cutoff: datetime, now: datetime) -> int:
    """Whole days since the latest seeded reading; never negative."""
    return max(0, math.floor((now - cutoff).total_seconds() / 86400.0))


def make_plan(*, cutoff: datetime, oldest: datetime, now: datetime) -> Plan:
    days = gap_days(cutoff, now)
    span = (cutoff - oldest).total_seconds() / 86400.0
    if days and days >= span:
        raise RollForwardError(
            f"{days} days to fill but only {span:.0f} days of seeded history to copy from. "
            "Use the smaller reseed instead (CLAUDE.md), and only if Aditya asks for it."
        )
    return Plan(cutoff=cutoff, oldest=oldest, days=days)


@dataclass(frozen=True)
class Operation:
    label: str
    table: str
    kind: str  # "copy", "delete" or "update"
    where: str
    body: str = ""
    # The land step moves the rows the park step moved; a dry run, which parks
    # nothing, reports the park step's count for it.
    same_rows_as: str | None = None

    def count_sql(self) -> str:
        return f"SELECT count(*) FROM {self.table} r WHERE {self.where}"

    def sql(self) -> str:
        if self.kind == "copy":
            return self.body.format(where=self.where)
        if self.kind == "delete":
            return f"DELETE FROM {self.table} r WHERE {self.where}"
        return f"UPDATE {self.table} r SET {self.body} WHERE {self.where}"


READING_COLUMNS = (
    "facility_id, sku_code, qty_on_hand, reported_at, source, footfall_same_day, "
    "reporter_ref, confidence, raw_payload"
)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "copy the last days forward",
        "stock_readings",
        "copy",
        "r.facility_id = ANY(:ids) AND r.source = 'seed' AND r.superseded_by IS NULL "
        "AND r.reported_at > :copy_from AND r.reported_at <= :cutoff",
        "INSERT INTO stock_readings (" + READING_COLUMNS + ") "
        "SELECT r.facility_id, r.sku_code, r.qty_on_hand, r.reported_at + :shift, r.source, "
        "r.footfall_same_day, r.reporter_ref, r.confidence, r.raw_payload "
        "FROM stock_readings r WHERE {where}",
    ),
    Operation(
        "trim the oldest days",
        "stock_readings",
        "delete",
        "r.facility_id = ANY(:ids) AND r.source = 'seed' AND r.reported_at < :trim_before "
        "AND NOT EXISTS (SELECT 1 FROM stock_readings s WHERE s.superseded_by = r.id)",
    ),
    Operation(
        "ward photo reports",
        "bed_reports",
        "update",
        "r.facility_id = ANY(:ids) AND r.reported_at <= :cutoff",
        "reported_at = r.reported_at + :shift",
    ),
    Operation(
        "staff check-ins",
        "staff_checkins",
        "update",
        "r.facility_id = ANY(:ids) AND r.checked_in_at <= :cutoff",
        "checked_in_at = r.checked_in_at + :shift, "
        "checked_out_at = r.checked_out_at + :shift",
    ),
    Operation(
        "re-verification pings",
        "staff_verifications",
        "update",
        "r.facility_id = ANY(:ids) AND r.sent_at <= :cutoff",
        "sent_at = r.sent_at + :shift, responded_at = r.responded_at + :shift",
    ),
    Operation(
        "warehouse dispatches",
        "medicine_movements",
        "update",
        # Seeded dispatches only: a movement an approval caused belongs to its
        # transfer, which does not move.
        "r.to_facility = ANY(:ids) AND r.transfer_id IS NULL AND r.dispatched_at <= :cutoff",
        "dispatched_at = r.dispatched_at + :shift, "
        "expected_by = r.expected_by + :shift, "
        "received_at = CASE WHEN r.received_at IS NULL THEN NULL "
        "WHEN r.received_at <= :cutoff THEN r.received_at + :shift "
        "ELSE LEAST(r.received_at + :shift, now()) END",
    ),
    # Codes are keyed by (facility, day), so they move in three steps: clear
    # the days they land on (codes issued on demand since the seed), park the
    # seed-era codes far ahead, then land them G days after where they were.
    Operation(
        "codes issued since the seed, on the days the seed's codes land",
        "verification_codes",
        "delete",
        "r.facility_id = ANY(:ids) AND r.for_date > :cutoff_date "
        "AND r.for_date <= :landing_date",
    ),
    Operation(
        "verification codes (park)",
        "verification_codes",
        "update",
        "r.facility_id = ANY(:ids) AND r.for_date <= :cutoff_date",
        f"for_date = r.for_date + {PARK_DAYS}",
    ),
    Operation(
        "verification codes (land)",
        "verification_codes",
        "update",
        "r.facility_id = ANY(:ids) AND r.for_date > :parked_after "
        "AND r.for_date - CAST(:park_days AS integer) <= :cutoff_date",
        f"for_date = r.for_date - {PARK_DAYS} + CAST(:days AS integer), "
        "issued_at = r.issued_at + :shift, delivered_at = r.delivered_at + :shift",
        same_rows_as="verification codes (park)",
    ),
)


def params(plan: Plan, ids: list[str]) -> dict:
    cutoff_date: date = plan.cutoff.date()
    return {
        "ids": ids,
        "cutoff": plan.cutoff,
        "cutoff_date": cutoff_date,
        "copy_from": plan.copy_from,
        "trim_before": plan.trim_before,
        "shift": plan.shift,
        "days": plan.days,
        "landing_date": cutoff_date + plan.shift,
        "parked_after": cutoff_date + timedelta(days=PARK_DAYS // 2),
        "park_days": PARK_DAYS,
    }


async def district_ids(session, state: str, district: str) -> list[str]:
    rows = await session.execute(
        text("SELECT id FROM facilities WHERE state_silo = :s AND district = :d ORDER BY id"),
        {"s": state, "d": district},
    )
    return [r[0] for r in rows.all()]


async def seeded_span(session, ids: list[str]) -> tuple[datetime | None, datetime | None]:
    row = (
        await session.execute(
            text(
                "SELECT max(reported_at), min(reported_at) FROM stock_readings "
                "WHERE facility_id = ANY(:ids) AND source = 'seed'"
            ),
            {"ids": ids},
        )
    ).one()
    return row[0], row[1]


async def bytes_per_row(session, table: str) -> float:
    """Heap plus indexes per row, from the catalogue; a guide, not a reading."""
    value = (
        await session.execute(
            text(
                "SELECT CASE WHEN c.reltuples > 0 "
                "THEN pg_total_relation_size(c.oid) / c.reltuples ELSE 0 END "
                "FROM pg_class c WHERE c.oid = CAST(:t AS regclass)"
            ),
            {"t": table},
        )
    ).scalar()
    return float(value or 0.0)


async def survey(session, plan: Plan, ids: list[str]) -> list[tuple[Operation, int]]:
    p = params(plan, ids)
    counts: dict[str, int] = {}
    out: list[tuple[Operation, int]] = []
    for op in OPERATIONS:
        if op.same_rows_as:
            n = counts[op.same_rows_as]
        else:
            n = int((await session.execute(text(op.count_sql()), p)).scalar() or 0)
        counts[op.label] = n
        out.append((op, n))
    return out


async def run(session, ids: list[str], *, confirm: bool, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff, oldest = await seeded_span(session, ids)
    if cutoff is None or oldest is None:
        print("no seeded readings in the district — nothing to roll forward")
        return 1
    try:
        plan = make_plan(cutoff=cutoff, oldest=oldest, now=now)
    except RollForwardError as exc:
        print(f"REFUSING: {exc}")
        return 1

    print(f"latest seeded reading : {cutoff:%Y-%m-%d %H:%M} UTC")
    print(f"oldest seeded reading : {oldest:%Y-%m-%d %H:%M} UTC")
    if plan.days == 0:
        print("\nless than a day since the latest seeded reading — nothing to do")
        return 0
    print(f"moving forward by     : {plan.days} days\n")

    counted = await survey(session, plan, ids)
    growth = 0.0
    for op, n in counted:
        print(f"  {op.kind:<7} {op.label:<66} {n:>8,}")
        if op.kind in ("copy", "update") and n:
            growth += n * await bytes_per_row(session, op.table)
    copied = next(n for op, n in counted if op.label == "copy the last days forward")
    trimmed = next(n for op, n in counted if op.label == "trim the oldest days")
    mb = growth / (1024 * 1024)
    print(f"\nreadings copied {copied:,} / trimmed {trimmed:,} (flat when these match)")
    print(
        f"new row versions ≈ {mb:.1f} MB ≈ {mb * GAUGE_FACTOR / GAUGE_MB * 100:.1f}% of the gauge "
        "until vacuum lets the space be reused; the write-ahead log adds a little, and recycles."
    )

    if not confirm:
        print("\nDry run: nothing written. Get the dashboard %, then re-run with --confirm.")
        return 0

    p = params(plan, ids)
    for op in OPERATIONS:
        await session.execute(text(op.sql()), p)
    await session.commit()
    print("\nmoved; rebuilding the map's rows and the trust copy for the district")
    await rebuild(session, ids)
    print("done. Ask for the dashboard % again and record both readings.")
    return 0


async def rebuild(session, ids: list[str]) -> None:
    from app import services, trust

    for facility_id in ids:
        await services.refresh_facility_state(session, facility_id)
    await session.commit()
    await trust.store(session, await trust.compute(session, trust.Scope(facility_ids=tuple(ids))))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true", help="write; without it this only reports")
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--district", default=DISTRICT)
    args = ap.parse_args()

    from app.db import SessionLocal

    async with SessionLocal() as session:
        ids = await district_ids(session, args.state, args.district)
        if not ids:
            print(f"no facilities in {args.district}, {args.state}")
            return 1
        print(f"district: {len(ids)} facilities in {args.district}, {args.state}")
        return await run(session, ids, confirm=args.confirm)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

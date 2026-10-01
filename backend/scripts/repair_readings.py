"""Withdraw readings that should never have been written — fix list #11r, #79.

Run by hand, dry run first:

    python -m scripts.remote --confirm -- -m scripts.repair_readings bill
    python -m scripts.remote --confirm -- -m scripts.repair_readings bill --confirm
    python -m scripts.remote --confirm -- -m scripts.repair_readings test-reports
    python -m scripts.remote --confirm -- -m scripts.repair_readings test-reports --confirm

(`scripts.remote --confirm` only lets the child reach the deployed database;
this script writes nothing without its own --confirm.)

Nothing is deleted. A bad reading is superseded: `superseded_by` points at the
reading that now stands in its place — the same centre's previous reading of
that medicine, or the row itself when there is none — and every read path
already ignores a superseded reading. The row stays, with a note in
raw_payload naming the fix that withdrew it, so the record of what happened
survives the repair. A second run finds nothing: only readings not yet
superseded match.

  bill          #11r. Nashik PHC 1's photographed bill, recorded before #11 as a
                stock count of 10 Paracetamol tablets. Written only when exactly
                one reading matches; otherwise the candidates are printed.
  test-reports  #79. "Send test report" before cbc8011 wrote an ORS count of 4
                over SMS (reporter test_...) at healthy centres anywhere in
                India. Only readings outside the Nashik sandbox are withdrawn —
                inside it the test report is the demo, and scripts.reset_nashik
                clears those. Bounded by date: nothing before the reseed.

Afterwards the map's stored rows are rebuilt for the affected centres only.
Read Render's dashboard % before and after (a handful of rows).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text

SANDBOX_STATE = "MH"
SANDBOX_DISTRICT = "Nashik"
# The last reseed of the deployed database; it truncated
# everything written before it.
RESEEDED_ON = date(2026, 9, 21)


@dataclass(frozen=True)
class Repair:
    fix: str
    where: str
    note: str


REPAIRS: dict[str, Repair] = {
    "bill": Repair(
        fix="#11r",
        where=(
            "f.state_silo = :sandbox_state AND f.district = :sandbox_district "
            "AND f.name = :facility_name AND r.sku_code = 'PARA500' AND r.source = 'photo' "
            "AND r.qty_on_hand = 10 AND r.superseded_by IS NULL"
        ),
        note="fix #11r: a photographed bill recorded as a stock count before #11",
    ),
    "test-reports": Repair(
        fix="#79",
        where=(
            "r.reported_at >= :since AND r.sku_code = 'ORS' AND r.source = 'sms' "
            "AND r.qty_on_hand = 4 AND r.reporter_ref LIKE 'test\\_%' "
            "AND NOT (f.state_silo = :sandbox_state AND f.district = :sandbox_district) "
            "AND r.superseded_by IS NULL"
        ),
        note="fix #79: a demo test report written outside the sandbox",
    ),
}

SUPERSEDE_SQL = """
UPDATE stock_readings r SET
    superseded_by = COALESCE((
        SELECT p.id FROM stock_readings p
        WHERE p.facility_id = r.facility_id AND p.sku_code = r.sku_code
          AND p.reported_at < r.reported_at AND p.superseded_by IS NULL AND p.id <> r.id
        ORDER BY p.reported_at DESC
        LIMIT 1
    ), r.id),
    raw_payload = COALESCE(r.raw_payload, CAST('{}' AS jsonb))
        || jsonb_build_object('withdrawn_by', CAST(:note AS text), 'withdrawn_at', now())
WHERE r.id = ANY(:ids)
"""


def may_write(repair: str, matches: int) -> bool:
    """The bill is one known reading: anything but exactly one is a surprise."""
    if repair == "bill":
        return matches == 1
    return matches >= 1


def select_sql(repair: Repair) -> str:
    return (
        "SELECT r.id, r.facility_id, f.name, r.sku_code, r.qty_on_hand, r.source, "
        "r.reported_at, r.reporter_ref "
        "FROM stock_readings r JOIN facilities f ON f.id = r.facility_id "
        f"WHERE {repair.where} ORDER BY r.reported_at"
    )


async def run(session, name: str, *, confirm: bool, facility_name: str, since: date) -> int:
    repair = REPAIRS[name]
    p = {
        "sandbox_state": SANDBOX_STATE,
        "sandbox_district": SANDBOX_DISTRICT,
        "facility_name": facility_name,
        "since": datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc),
    }
    rows = (await session.execute(text(select_sql(repair)), p)).all()
    print(f"{name} ({repair.fix}): {len(rows)} reading(s) not yet withdrawn")
    for r in rows:
        print(
            f"  #{r.id}  {r.name:<28} {r.sku_code:<8} {float(r.qty_on_hand):>8g}  "
            f"{r.source:<6} {r.reported_at:%Y-%m-%d %H:%M}  {r.reporter_ref or ''}"
        )
    if not rows:
        print("nothing to repair — already done, or never written here")
        return 0
    if not may_write(name, len(rows)):
        print("REFUSING: expected exactly one reading. Nothing written; check the list above.")
        return 1
    if not confirm:
        print("\nDry run: nothing written. Get the dashboard %, then re-run with --confirm.")
        return 0

    ids = [r.id for r in rows]
    await session.execute(text(SUPERSEDE_SQL), {"ids": ids, "note": repair.note})
    await session.commit()

    from app import services

    for facility_id in sorted({r.facility_id for r in rows}):
        await services.refresh_facility_state(session, facility_id)
    await session.commit()
    print(f"\nwithdrew {len(ids)} reading(s); the map's rows rebuilt for those centres")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repair", choices=sorted(REPAIRS))
    ap.add_argument("--confirm", action="store_true", help="write; without it this only reports")
    ap.add_argument("--facility-name", default="Nashik PHC 1", help="bill: the centre")
    ap.add_argument(
        "--since", type=date.fromisoformat, default=RESEEDED_ON, help="test-reports: from this day"
    )
    args = ap.parse_args()

    from app.db import SessionLocal

    async with SessionLocal() as session:
        return await run(
            session,
            args.repair,
            confirm=args.confirm,
            facility_name=args.facility_name,
            since=args.since,
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

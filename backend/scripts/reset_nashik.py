"""Put the Nashik demo sandbox back the way the seed left it.

    python -m scripts.reset_nashik            # show what would be removed
    python -m scripts.reset_nashik --confirm  # remove it

The live loop writes: it commits a stock reading, asks the solver for a plan,
approves a transfer, and dispatches a batch. Every one of those is a real row,
which is the point — but after a dozen rehearsals the district no longer looks
like the district the seed built, and the demo stops being reproducible.

Restoring means restoring *to* the seed, not below it. The seed writes ward
photos, warehouse dispatches and three weeks of readings for this district too,
so each table needs the line between a seeded row and a demo one:

  stock_readings      source = 'seed' is baseline; sms, form, voice, transfer
                      and the rest arrived later
  medicine_movements  the seed dispatches from a warehouse with no transfer;
                      only a movement carrying a transfer_id came from an
                      approval in the app
  bed_reports         the seed stops at yesterday, so today's are the demo's
                      (a reset run within three hours of midnight can catch
                      one seeded report — harmless, and it reappears on reseed)
  transfers           none are seeded; the plan is re-solved afterwards

It never touches a facility outside MH/Nashik, and it never removes an approved
transfer without the movement that approval caused, because a half-undone
approval leaves the ledger claiming a delivery with no origin. It does discard
decided transfers, which `generate_plan` itself would preserve as history —
that is the difference between a sandbox and a district.

Verification codes are left alone. They are one cheap row per facility-day, the
bed reports already written reference them, and regenerating one would change a
code some existing row was checked against.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import redistribution, services
from app.db import SessionLocal
from app.models import (
    Approval,
    BedReport,
    Facility,
    MedicineMovement,
    StockReading,
    Transfer,
)

STATE = "MH"
DISTRICT = "Nashik"

# The seed's own source tag. Rows carrying it are the baseline; everything else
# in this district arrived from a demo, a rehearsal or the live loop.
SEED_SOURCE = "seed"


async def _facility_ids(session: AsyncSession) -> list[str]:
    return list(
        (
            await session.execute(
                select(Facility.id).where(
                    Facility.state_silo == STATE, Facility.district == DISTRICT
                )
            )
        )
        .scalars()
        .all()
    )


async def _count(session: AsyncSession, sql: str, **params) -> int:
    return int((await session.execute(text(sql), params)).scalar() or 0)


async def survey(session: AsyncSession, ids: list[str]) -> dict[str, int]:
    """What a reset would remove, counted before anything is deleted."""
    p = {"ids": ids, "seed": SEED_SOURCE}
    return {
        "stock readings": await _count(
            session,
            "SELECT count(*) FROM stock_readings WHERE facility_id = ANY(:ids) "
            "AND source <> :seed",
            **p,
        ),
        "bed reports (today)": await _count(
            session,
            "SELECT count(*) FROM bed_reports WHERE facility_id = ANY(:ids) "
            "AND reported_at >= CURRENT_DATE",
            **p,
        ),
        # Only decided transfers count as residue. Open proposals are the
        # solver's current answer, not something a demo left behind: they are
        # deleted and re-solved every run, so counting them would mean a clean
        # sandbox never reported itself clean.
        "decided transfers": await _count(
            session,
            "SELECT count(*) FROM transfers WHERE (to_facility = ANY(:ids) "
            "OR from_facility = ANY(:ids)) AND status <> 'proposed'",
            **p,
        ),
        "transfer movements": await _count(
            session,
            "SELECT count(*) FROM medicine_movements WHERE to_facility = ANY(:ids) "
            "AND transfer_id IS NOT NULL",
            **p,
        ),
    }


async def reset(session: AsyncSession, ids: list[str]) -> None:
    # Approvals and movements first: both point at a transfer, and a transfer
    # removed from under them would orphan the ledger's account of why the
    # stock moved.
    transfer_ids = list(
        (
            await session.execute(
                select(Transfer.id).where(
                    (Transfer.to_facility.in_(ids)) | (Transfer.from_facility.in_(ids))
                )
            )
        )
        .scalars()
        .all()
    )
    if transfer_ids:
        await session.execute(delete(Approval).where(Approval.transfer_id.in_(transfer_ids)))
        await session.execute(
            delete(MedicineMovement).where(MedicineMovement.transfer_id.in_(transfer_ids))
        )
        await session.execute(delete(Transfer).where(Transfer.id.in_(transfer_ids)))

    # Any remaining movement born of an approval this script did not already
    # reach — a transfer decided before its recipient moved into the sandbox.
    await session.execute(
        delete(MedicineMovement).where(
            MedicineMovement.to_facility.in_(ids), MedicineMovement.transfer_id.is_not(None)
        )
    )
    await session.execute(
        delete(BedReport).where(
            BedReport.facility_id.in_(ids),
            BedReport.reported_at >= text("CURRENT_DATE"),
        )
    )
    await session.execute(
        delete(StockReading).where(
            StockReading.facility_id.in_(ids), StockReading.source != SEED_SOURCE
        )
    )
    await session.commit()

    # Rebuild the snapshot the map reads, from the readings that remain. Same
    # function the ingestion spine calls after every commit, so the restored
    # numbers are produced the same way the live ones are.
    for facility_id in ids:
        await services.refresh_facility_state(session, facility_id)
    await session.commit()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--confirm", action="store_true", help="actually delete; without it this only reports"
    )
    args = ap.parse_args()

    async with SessionLocal() as session:
        ids = await _facility_ids(session)
        if not ids:
            print(f"no facilities in {DISTRICT}, {STATE} — nothing to reset")
            return 1
        print(f"sandbox: {len(ids)} facilities in {DISTRICT}, {STATE}")

        counts = await survey(session, ids)
        total = sum(counts.values())
        for label, n in counts.items():
            print(f"  {label:<20} {n:>7,}")
        if total == 0:
            print("\nalready clean")
            return 0

        if not args.confirm:
            print(f"\n{total:,} rows would be removed. Re-run with --confirm.")
            return 0

        await reset(session, ids)
        print(f"\nremoved {total:,} rows; snapshots rebuilt from the seeded readings")

        plan = await redistribution.generate_plan(session, STATE)
        await session.commit()
        print(f"re-solved {STATE}: {len(plan.transfer_ids)} proposals ({plan.solver})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Drop `ix_stock_readings_facility_id` from the deployed database.

    python -m scripts.remote --confirm -- -m scripts.drop_redundant_index
    python -m scripts.remote --confirm -- -m scripts.drop_redundant_index --dry-run

One index, named in the file, on one table. It is a strict prefix of
`ix_readings_facility_sku_time (facility_id, sku_code, reported_at)`, so every
lookup it can serve the composite serves from its leading column — and the
deployed statistics agreed: 6 scans against the composite's 293.

`DROP INDEX` is the right tool on a volume this full. It releases the file
immediately, needs no free space to work in (unlike `VACUUM FULL`, which
rewrites the table), and is reversible with one `CREATE INDEX`.

The definition is read and printed *before* the drop, so the exact statement
that would restore it is in the run's own output rather than only in a
migration file somebody has to go and find.

This does not stamp Alembic. Migration e4d7a9c31b52 performs the same drop as
`DROP INDEX IF EXISTS`, so it stays correct whether it runs before this script,
after it, or never.
"""

from __future__ import annotations

import argparse
import asyncio
import os

INDEX = "ix_stock_readings_facility_id"
TABLE = "stock_readings"


def _dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("DATABASE_URL is not set; run this through scripts.remote")
    # asyncpg wants a plain libpq URL, not SQLAlchemy's driver-qualified one.
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run", action="store_true", help="report what would be dropped and stop"
    )
    args = ap.parse_args()

    import asyncpg

    conn = await asyncpg.connect(_dsn(), timeout=60)
    try:
        definition = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = $1 AND indexname = $2",
            TABLE,
            INDEX,
        )
        if definition is None:
            print("{0} is not present — nothing to do.".format(INDEX))
            return 0

        size, scans = await conn.fetchrow(
            "SELECT pg_size_pretty(pg_relation_size(i.indexrelid)), i.idx_scan "
            "FROM pg_stat_user_indexes i WHERE i.indexrelname = $1",
            INDEX,
        )
        before = await conn.fetchval("SELECT pg_database_size(current_database())")

        print("  index      : {0}".format(INDEX))
        print("  size       : {0}   scans since stats reset: {1}".format(size, scans))
        print("  definition : {0}".format(definition))
        print("  restore it : {0}".format(definition))
        print("  db before  : {0:,} bytes ({1:.1f} MB)".format(before, before / 1024 ** 2))

        if args.dry_run:
            print("\n  dry run — nothing dropped")
            return 0

        await conn.execute("DROP INDEX {0}".format(INDEX))
        after = await conn.fetchval("SELECT pg_database_size(current_database())")
        freed = before - after

        print("\n  dropped.")
        print("  db after   : {0:,} bytes ({1:.1f} MB)".format(after, after / 1024 ** 2))
        print("  freed      : {0:,} bytes ({1:.1f} MB)".format(freed, freed / 1024 ** 2))
        # The dashboard counts a filesystem, not this number; the factor is the
        # one calibration we have (see docs/STORAGE_NOTES.md).
        print(
            "  on the dashboard that is about {0:.1f} MB, roughly {1:.1f} "
            "percentage points of 1 GB".format(
                freed * 1.29 / 1024 ** 2, freed * 1.29 / 1024 ** 3 * 100
            )
        )
        print("\n  Ask Aditya for the dashboard percentage now.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Run a management command against the deployed database, guarded.

    python -m scripts.remote --show
    python -m scripts.remote --counts
    python -m scripts.remote --diagnose
    python -m scripts.remote --confirm -- -m scripts.seed --days 35 --focus-days 120

The deployed database is reached only through RENDER_DATABASE_URL_EXTERNAL, and
never through DATABASE_URL. That separation is the whole point of this file: the
seed truncates what it finds, and a single mistyped variable is the difference
between reseeding a demo and destroying the local database that holds the
federation run.

So: the target host is printed before anything runs, a local host is refused
outright, and any command that could write needs --confirm. The connection
string itself is never printed, logged or passed on a command line — it goes to
the child process in its environment.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

BACKEND_ROOT = Path(__file__).resolve().parent.parent
VARIABLE = "RENDER_DATABASE_URL_EXTERNAL"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Read-only, and worth having in one place: this is what "did the seed work?"
# actually means.
COUNT_TABLES = (
    "facilities",
    "skus",
    "stock_readings",
    "facility_sku_state",
    "bed_reports",
    "staff_checkins",
    "medicine_movements",
    "facility_trust",
    "forecasts",
    "federation_rounds",
    "users",
)


def load_dsn() -> str:
    """The deployed DSN, from the environment or from .env. Never printed."""
    value = os.environ.get(VARIABLE)
    if not value:
        for candidate in (BACKEND_ROOT.parent / ".env", BACKEND_ROOT / ".env"):
            if not candidate.is_file():
                continue
            for line in candidate.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(VARIABLE + "="):
                    value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
    if not value:
        raise SystemExit(
            "{0} is not set. Put Render's External Database URL in .env under that "
            "name — not in DATABASE_URL.".format(VARIABLE)
        )
    return value


def describe(dsn: str) -> str:
    """Print who we are about to talk to. Returns the hostname."""
    parts = urlsplit(dsn)
    host = parts.hostname or ""
    print("  target host :", host or "(none)")
    print("  port        :", parts.port or 5432)
    print("  database    :", (parts.path or "/").lstrip("/") or "(none)")
    print("  credentials : present, not shown" if parts.username else "  credentials : NONE")
    if host in LOCAL_HOSTS:
        raise SystemExit(
            "REFUSING: {0} points at a local host. This command is only for the "
            "deployed database.".format(VARIABLE)
        )
    return host


async def counts(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        existing = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        print("\n  rows")
        for table in COUNT_TABLES:
            if table not in existing:
                print("    {0:22s} (table does not exist)".format(table))
                continue
            n = await conn.fetchval("SELECT count(*) FROM {0}".format(table))
            print("    {0:22s} {1:>12,}".format(table, n))
        size = await conn.fetchval("SELECT pg_database_size(current_database())")
        print("\n  database size : {0:,} bytes  ({1:.1f} MB, {2:.1%} of the 1 GB free limit)".format(
            size, size / 1024 ** 2, size / 1024 ** 3
        ))
    finally:
        await conn.close()


# ---------------------------------------------------------------- diagnose ---
# Read-only, and deliberately on this side of the --confirm gate: every
# statement below is a SELECT against a catalogue or a statistics view.
#
# It exists because pg_database_size and a host's disk gauge answer different
# questions. pg_database_size counts one database's relations; the gauge counts
# a filesystem, which also holds every other database on the instance, the
# write-ahead log, temporary files and the server's own logs. When the two
# disagree the difference is usually WAL, and WAL is usually recyclable — but a
# forgotten replication slot pins it forever, so that is asked about too.
DIAGNOSTICS: tuple[tuple[str, str], ...] = (
    (
        "this database",
        "SELECT current_database() AS name, "
        "pg_size_pretty(pg_database_size(current_database())) AS size, "
        "pg_database_size(current_database()) AS bytes",
    ),
    (
        "every database on the instance",
        "SELECT datname AS name, pg_size_pretty(pg_database_size(datname)) AS size, "
        "pg_database_size(datname) AS bytes FROM pg_database "
        "WHERE datallowconn ORDER BY pg_database_size(datname) DESC",
    ),
    (
        "largest relations (table + indexes + toast)",
        "SELECT relname AS name, "
        "pg_size_pretty(pg_total_relation_size(c.oid)) AS total, "
        "pg_size_pretty(pg_table_size(c.oid)) AS heap, "
        "pg_size_pretty(pg_indexes_size(c.oid)) AS indexes, "
        "pg_total_relation_size(c.oid) AS bytes "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','m','p') "
        "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 15",
    ),
    (
        "dead tuples (space a VACUUM could reuse, not release)",
        "SELECT relname AS name, n_live_tup AS live, n_dead_tup AS dead, "
        "CASE WHEN n_live_tup > 0 THEN round(100.0 * n_dead_tup / n_live_tup, 1) "
        "ELSE NULL END AS dead_pct, last_vacuum, last_autovacuum "
        "FROM pg_stat_user_tables WHERE n_dead_tup > 0 "
        "ORDER BY n_dead_tup DESC LIMIT 15",
    ),
    (
        "write-ahead log",
        "SELECT count(*) AS files, pg_size_pretty(sum(size)) AS size, "
        "sum(size) AS bytes FROM pg_ls_waldir()",
    ),
    (
        "WAL settings (what the server is allowed to keep)",
        "SELECT name, setting, unit FROM pg_settings WHERE name IN "
        "('min_wal_size','max_wal_size','wal_keep_size','archive_mode',"
        "'max_slot_wal_keep_size','wal_level','checkpoint_timeout')",
    ),
    (
        "replication slots (an inactive one pins WAL forever)",
        "SELECT slot_name, slot_type, active, "
        "pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_held "
        "FROM pg_replication_slots",
    ),
    (
        "temporary files written",
        "SELECT datname AS name, temp_files, pg_size_pretty(temp_bytes) AS temp, "
        "pg_size_pretty(COALESCE(blks_read,0) * 0) AS unused FROM pg_stat_database "
        "WHERE temp_files > 0 ORDER BY temp_bytes DESC",
    ),
    (
        "prepared transactions (these also hold storage)",
        "SELECT gid, prepared, database FROM pg_prepared_xacts",
    ),
    (
        "stock_readings: what is in it and how old",
        "SELECT count(*) AS rows, min(reported_at)::date AS oldest, "
        "max(reported_at)::date AS newest, "
        "count(*) FILTER (WHERE reported_at >= now() - interval '60 days') AS last_60d, "
        "count(*) FILTER (WHERE reported_at >= now() - interval '28 days') AS last_28d "
        "FROM stock_readings",
    ),
    (
        "indexes on stock_readings (they outweigh the table itself)",
        "SELECT indexrelname AS name, pg_size_pretty(pg_relation_size(indexrelid)) AS size, "
        "idx_scan AS scans FROM pg_stat_user_indexes WHERE relname = 'stock_readings' "
        "ORDER BY pg_relation_size(indexrelid) DESC",
    ),
    (
        "index definitions (is a small one already covered by a big one?)",
        "SELECT indexname AS name, replace(indexdef, 'CREATE INDEX ', '') AS definition "
        "FROM pg_indexes WHERE tablename = 'stock_readings' ORDER BY indexname",
    ),
    (
        "shape of the deployed data",
        "SELECT count(DISTINCT state_silo) AS regions, count(DISTINCT district) AS districts, "
        "count(*) AS facilities FROM facilities",
    ),
    (
        "server and role",
        "SELECT version() AS version, current_user AS role, "
        "pg_is_in_recovery() AS replica",
    ),
    (
        "checkpoints and WAL written since the stats were reset",
        "SELECT checkpoints_timed, checkpoints_req, stats_reset FROM pg_stat_bgwriter",
    ),
    (
        "oldest transaction holding rows back from vacuum",
        "SELECT pid, state, xact_start, query_start, left(query, 60) AS query "
        "FROM pg_stat_activity WHERE xact_start IS NOT NULL "
        "ORDER BY xact_start LIMIT 5",
    ),
)


async def diagnose(dsn: str) -> None:
    """Where the disk actually went. Every statement is a SELECT."""
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=60)
    try:
        for title, sql in DIAGNOSTICS:
            print()
            print("  {0}".format(title))
            print("  " + "-" * len(title))
            try:
                rows = await conn.fetch(sql)
            except Exception as exc:  # permission, missing view, older server
                print("    unavailable: {0}".format(str(exc).splitlines()[0]))
                continue
            if not rows:
                print("    (none)")
                continue
            for row in rows:
                print(
                    "    "
                    + "  ".join(
                        "{0}={1}".format(k, "-" if v is None else v) for k, v in row.items()
                    )
                )

        # The part a per-table sum cannot see: catalogues, free space maps and
        # whatever the database holds that is not a public relation.
        try:
            total = await conn.fetchval("SELECT pg_database_size(current_database())")
            relations = await conn.fetchval(
                "SELECT COALESCE(sum(pg_total_relation_size(c.oid)), 0) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                # pg_total_relation_size already folds in indexes and TOAST, so
                # counting an index row as well would add it twice.
                "AND c.relkind IN ('r','m','p')"
            )
            print()
            print("  accounting")
            print("  " + "-" * len("accounting"))
            print("    public relations : {0:>12,} bytes  ({1:.1f} MB)".format(
                relations, relations / 1024 ** 2))
            print("    everything else  : {0:>12,} bytes  ({1:.1f} MB)".format(
                total - relations, (total - relations) / 1024 ** 2))
            print("    database total   : {0:>12,} bytes  ({1:.1f} MB)".format(
                total, total / 1024 ** 2))
            print()
            print("    A host's disk gauge counts more than this number: other")
            print("    databases, the write-ahead log, temp files and server logs")
            print("    all sit on the same filesystem.")
        except Exception as exc:
            print("    accounting unavailable: {0}".format(str(exc).splitlines()[0]))
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--show", action="store_true", help="print the target and stop")
    p.add_argument("--counts", action="store_true", help="row counts and database size")
    p.add_argument(
        "--diagnose",
        action="store_true",
        help="read-only: where the disk went (sizes, bloat, WAL, temp files)",
    )
    p.add_argument("--confirm", action="store_true", help="required for anything that writes")
    p.add_argument("--python", default=sys.executable, help="interpreter to run (the Flower env has torch)")
    p.add_argument("--cwd", default=None, help="working directory, relative to backend/")
    # The federation scripts read their own variable, because a SuperNode is
    # handed one silo's database and nothing else.
    p.add_argument("--env-name", default="DATABASE_URL", help="variable the child reads the DSN from")
    p.add_argument("rest", nargs=argparse.REMAINDER, help="-- followed by arguments for python")
    args = p.parse_args()

    dsn = load_dsn()
    print("remote target:")
    describe(dsn)

    if args.show:
        return
    if args.counts:
        asyncio.run(counts(dsn))
        return
    if args.diagnose:
        asyncio.run(diagnose(dsn))
        return

    command = [a for a in args.rest if a != "--"]
    if not command:
        raise SystemExit("Nothing to run. Pass -- followed by arguments for python.")
    if not args.confirm:
        raise SystemExit(
            "REFUSING: this would run against the deployed database. Re-run with "
            "--confirm once the host above is the one you meant."
        )

    cwd = BACKEND_ROOT if args.cwd is None else (BACKEND_ROOT / args.cwd)
    print("\n  interpreter : {0}".format(args.python))
    print("  working dir : {0}".format(cwd))
    print("  dsn passed as {0}".format(args.env_name))
    print("\n  running: {0}\n".format(" ".join(command)))
    # The DSN reaches the child in its environment, never on a command line,
    # because command lines are visible to every other process on the machine.
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        PYTHONPATH=str(cwd),
    )
    # Only the one the child was told to read. Setting both would mean a script
    # that reached for the wrong variable still found a live database.
    env.pop("DATABASE_URL", None)
    env.pop("SWASTHSETU_DATABASE_URL", None)
    env.pop(VARIABLE, None)
    env[args.env_name] = dsn
    raise SystemExit(
        subprocess.run([args.python, *command], cwd=str(cwd), env=env).returncode
    )


if __name__ == "__main__":
    main()

"""Run a management command against the deployed database, guarded.

    python -m scripts.remote --show
    python -m scripts.remote --counts
    python -m scripts.remote --diagnose
    python -m scripts.remote --connections
    python -m scripts.remote --terminate 12345 --confirm
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
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DEV_JWT_SECRET, DEV_PHONE_SALT  # noqa: E402

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


# Secrets whose value changes what a remote command *writes*, mapped to the
# development default that must never reach the deployed database.
#
# This list exists because of a real failure. `facility_contacts` stores a
# salted hash of each handset and nothing else. A reseed was run against the
# deployed database from a laptop whose .env carried no PHONE_HASH_SALT, so
# every row was hashed with DEV_PHONE_SALT while the deployed service hashes
# with its own — and every registered handset became unreachable. Nothing
# failed. The seed reported success, the table filled, and the ingestion spine
# simply answered "this number is not registered to a facility" forever after.
#
# So a command that could write now has to say which salt it means. Inheriting
# whatever happens to be in the local environment is exactly how a value that
# belongs to one environment ends up baked into another's data.
REMOTE_CRITICAL_SECRETS: dict[str, str] = {
    "PHONE_HASH_SALT": DEV_PHONE_SALT,
    "JWT_SECRET": DEV_JWT_SECRET,
}


def fingerprint(value: str) -> str:
    """Eight characters identifying a secret without disclosing it, so the
    operator can compare what is about to be used against what the deployed
    service uses."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def require_remote_secrets() -> dict[str, str]:
    """Refuse to run a writing command without the deployed environment's own
    secrets, and refuse just as loudly if they are still the dev defaults.

    Returns the values, so the caller can pass exactly these and nothing else.
    """
    missing: list[str] = []
    dev: list[str] = []
    resolved: dict[str, str] = {}
    source: dict[str, str] = {}

    for name, dev_default in REMOTE_CRITICAL_SECRETS.items():
        # The shell first: setting it inline for one command is the most
        # deliberate way to say "this value is for this run". .env second,
        # because that is where an operator reasonably keeps it — but which
        # of the two answered is printed, so provenance is never a guess.
        value = os.environ.get(name) or _dotenv_values().get(name, "")
        if not value:
            missing.append(name)
        elif value == dev_default:
            dev.append(name)
        else:
            resolved[name] = value
            source[name] = 'shell' if os.environ.get(name) else '.env'

    if missing or dev:
        print()
        print("  REFUSING: this command can write to the deployed database, and")
        print("  the secrets that decide what it writes are not safe to guess.")
        for name in missing:
            print("    {0:<18} not set".format(name))
        for name in dev:
            print("    {0:<18} still the development default".format(name))
        print()
        print("  Set each to the value the deployed service uses — Render")
        print("  dashboard, Environment — and run this again. They are never")
        print("  printed; only an eight-character fingerprint is, so the value")
        print("  here can be compared with the value there.")
        raise SystemExit(
            "missing or development-default secrets: {0}".format(
                ", ".join(sorted(missing + dev))
            )
        )

    print("\n  secrets (fingerprints, never values)")
    for name, value in sorted(resolved.items()):
        print("    {0:<18} {1}   from {2}".format(
            name, fingerprint(value), source.get(name, "?")
        ))
    print("  Compare each fingerprint with the deployed service before writing.")
    return resolved


def _dotenv_values() -> dict[str, str]:
    """Secrets as .env states them.

    Read directly rather than through Settings, so this guard never
    depends on the configuration it exists to guard.
    """
    found: dict[str, str] = {}
    for candidate in (BACKEND_ROOT.parent / '.env', BACKEND_ROOT / '.env'):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, _, value = stripped.partition('=')
            if key.strip() in REMOTE_CRITICAL_SECRETS:
                found.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return found


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


# ------------------------------------------------------------ connections ---
# Read-only, on this side of the --confirm gate: it reads pg_stat_activity and
# pg_locks and nothing else. It exists because an old transaction is invisible
# in every size measurement and yet changes what those measurements mean —
# vacuum cannot reclaim a dead tuple younger than the oldest running
# transaction, so a connection somebody left open a day ago quietly pins bloat
# in place while `--counts` reports a number that looks fine.


async def connections(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        me = await conn.fetchval("SELECT pg_backend_pid()")
        rows = await conn.fetch(
            """
            SELECT pid, usename, state, backend_type, xact_start,
                   now() - xact_start   AS xact_age,
                   now() - state_change AS idle_for,
                   wait_event_type,
                   left(query, 70)      AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
            ORDER BY xact_start NULLS LAST, query_start
            """
        )
        print("")
        print("  backends on this database")
        print("  " + "-" * 70)
        for r in rows:
            mine = "  (this session)" if r["pid"] == me else ""
            print("    pid={0}{1}".format(r["pid"], mine))
            print(
                "      state={0}  type={1}  wait={2}".format(
                    r["state"], r["backend_type"], r["wait_event_type"]
                )
            )
            print(
                "      xact_start={0}  age={1}".format(r["xact_start"], r["xact_age"])
            )
            print("      idle_for={0}".format(r["idle_for"]))
            print("      query={0}".format(r["query"]))

        print("")
        print("  locks held by other backends (is anything live behind them?)")
        print("  " + "-" * 70)
        locks = await conn.fetch(
            """
            SELECT l.pid, l.locktype, l.mode, l.granted,
                   coalesce(c.relname, '-') AS relation
            FROM pg_locks l
            LEFT JOIN pg_class c ON c.oid = l.relation
            WHERE l.pid <> pg_backend_pid()
            ORDER BY l.pid
            """
        )
        if not locks:
            print("    (none)")
        for r in locks:
            print(
                "    pid={0}  {1} {2} on {3}  granted={4}".format(
                    r["pid"], r["locktype"], r["mode"], r["relation"], r["granted"]
                )
            )
    finally:
        await conn.close()


async def terminate(dsn: str, pid: int) -> None:
    """End one backend. Behind --confirm, because it ends whatever it was doing.

    Everything that can be learned about the victim is printed before the kill,
    and a backend that is actually executing a statement is refused rather than
    killed: an idle-in-transaction connection is safe to end, a query in flight
    is a decision for a person and not for this script.
    """
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        row = await conn.fetchrow(
            """
            SELECT pid, usename, state, backend_type, xact_start, query_start,
                   now() - xact_start   AS xact_age,
                   now() - state_change AS idle_for,
                   left(query, 200)     AS query
            FROM pg_stat_activity
            WHERE pid = $1 AND datname = current_database()
            """,
            pid,
        )
        if row is None:
            print("")
            print("  pid {0} is not connected to this database.".format(pid))
            print("  Nothing to terminate.")
            return

        held = await conn.fetch(
            """
            SELECT l.locktype, l.mode, coalesce(c.relname, '-') AS relation, l.granted
            FROM pg_locks l
            LEFT JOIN pg_class c ON c.oid = l.relation
            WHERE l.pid = $1
            """,
            pid,
        )
        blocking = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE $1 = ANY(pg_blocking_pids(pid))",
            pid,
        )

        print("")
        print("  about to terminate")
        print("    pid        : {0}".format(row["pid"]))
        print("    user       : {0}".format(row["usename"]))
        print("    state      : {0}".format(row["state"]))
        print("    type       : {0}".format(row["backend_type"]))
        print(
            "    xact_start : {0}   (age {1})".format(row["xact_start"], row["xact_age"])
        )
        print("    idle_for   : {0}".format(row["idle_for"]))
        print("    query      : {0}".format(row["query"]))
        print("    locks      : {0}".format(len(held)))
        for h in held:
            print(
                "      {0} {1} on {2}  granted={3}".format(
                    h["locktype"], h["mode"], h["relation"], h["granted"]
                )
            )
        print("    blocking   : {0} other backend(s)".format(blocking))

        # A live statement is not this script's to cancel. "active" here means
        # a query is executing right now, not merely that a transaction is open.
        if row["state"] == "active":
            print("")
            print("  REFUSING: pid {0} is running a statement right now.".format(pid))
            print("  Terminating it would abort work in flight. Re-check with")
            print("  --connections, and kill it by hand if that is really meant.")
            return

        killed = await conn.fetchval("SELECT pg_terminate_backend($1)", pid)
        print("")
        print("  pg_terminate_backend returned {0}".format(killed))

        still = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE pid = $1", pid
        )
        print("  pid {0} still present : {1}".format(pid, bool(still)))

        oldest = await conn.fetchrow(
            """
            SELECT pid, now() - xact_start AS age, state
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND xact_start IS NOT NULL
              AND pid <> pg_backend_pid()
            ORDER BY xact_start
            LIMIT 1
            """
        )
        if oldest is None:
            print("  oldest remaining transaction : none, other than this session")
        else:
            print(
                "  oldest remaining transaction : pid {0}, state {1}, age {2}".format(
                    oldest["pid"], oldest["state"], oldest["age"]
                )
            )
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
    p.add_argument(
        "--connections",
        action="store_true",
        help="read-only: backends, transaction ages and locks",
    )
    p.add_argument(
        "--terminate",
        type=int,
        metavar="PID",
        help="end one backend (needs --confirm); prints it first, refuses a live query",
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
    if args.connections:
        asyncio.run(connections(dsn))
        return
    if args.terminate is not None:
        if not args.confirm:
            raise SystemExit(
                "REFUSING: terminating a backend ends whatever it was doing. "
                "Re-run with --confirm once the host above is the one you meant."
            )
        asyncio.run(terminate(dsn, args.terminate))
        return

    command = [a for a in args.rest if a != "--"]
    if not command:
        raise SystemExit("Nothing to run. Pass -- followed by arguments for python.")
    if not args.confirm:
        raise SystemExit(
            "REFUSING: this would run against the deployed database. Re-run with "
            "--confirm once the host above is the one you meant."
        )

    # Checked before anything is printed about running, so a refusal
    # arrives before the operator has reason to think it started.
    secrets = require_remote_secrets()

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
        **secrets,
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

"""Run a management command against the deployed database, guarded.

    python -m scripts.remote --show
    python -m scripts.remote --counts
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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--show", action="store_true", help="print the target and stop")
    p.add_argument("--counts", action="store_true", help="row counts and database size")
    p.add_argument("--confirm", action="store_true", help="required for anything that writes")
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

    command = [a for a in args.rest if a != "--"]
    if not command:
        raise SystemExit("Nothing to run. Pass -- followed by arguments for python.")
    if not args.confirm:
        raise SystemExit(
            "REFUSING: this would run against the deployed database. Re-run with "
            "--confirm once the host above is the one you meant."
        )

    print("\n  running: python {0}\n".format(" ".join(command)))
    # The DSN reaches the child in its environment, never on a command line,
    # because command lines are visible to every other process on the machine.
    env = dict(os.environ, DATABASE_URL=dsn, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.pop(VARIABLE, None)
    raise SystemExit(
        subprocess.run([sys.executable, *command], cwd=str(BACKEND_ROOT), env=env).returncode
    )


if __name__ == "__main__":
    main()

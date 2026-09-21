"""Measure what the app's heaviest read paths cost the deployed disk.

    python -m scripts.remote --confirm -- -m scripts.probe_reads

Read-only, and not on trust: this opens its own engine with
`default_transaction_read_only` set on the server, so a write is refused by
Postgres rather than by a promise in a docstring. It goes through the guarded
runner only because the runner cannot tell a read-only script from a seed.

The question it answers is narrow. A SELECT cannot grow a table, but it can
still consume disk: a sort or a hash join larger than `work_mem` spills to a
temporary file, and on a volume with little headroom a big enough spill is the
difference between a slow page and a server that stops. `pg_stat_database`
counts those spills per database, so sampling it around each path attributes
them to the query that caused them.

`temp_bytes` is cumulative and never decreases; a temporary file is deleted
when its query ends. So a non-zero delta here is not disk still held, it is
disk that was held for the duration — which is what matters when the headroom
is smaller than the spill.
"""

from __future__ import annotations

import asyncio
import functools
import os
import sys
import time

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import aggregates, redistribution, services, trust
from app.models import FederationRound, Sku

SANDBOX_STATE = "MH"
SANDBOX_DISTRICT = "Nashik"
# Roughly the whole country, which is what the map asks for when somebody
# zooms out before the tier switches back to rollups.
INDIA_BBOX = (6.4, 67.8, 36.2, 97.6)


print = functools.partial(__builtins__["print"] if isinstance(__builtins__, dict)
                          else __builtins__.print, flush=True)

# No single path may hold the run. One that cannot finish in this long
# against the deployed database has told us what we came to find out.
PATH_TIMEOUT_S = 120.0


def _dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("DATABASE_URL is not set; run this through scripts.remote")
    # SQLAlchemy needs the async driver named explicitly.
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql+asyncpg://", 1)
    elif value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


async def _temp(session) -> tuple[int, int]:
    row = (
        await session.execute(
            text(
                "SELECT temp_files, temp_bytes FROM pg_stat_database "
                "WHERE datname = current_database()"
            )
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def main() -> int:
    engine = create_async_engine(
        _dsn(),
        echo=False,
        pool_pre_ping=True,
        # The guarantee. Any INSERT, UPDATE, DELETE or DDL on this connection
        # is refused by the server with a read-only transaction error.
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    Session = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with Session() as session:
        settings_rows = (
            await session.execute(
                text(
                    "SELECT name, setting, unit FROM pg_settings WHERE name IN "
                    "('work_mem','maintenance_work_mem','temp_file_limit',"
                    "'hash_mem_multiplier','shared_buffers')"
                )
            )
        ).all()
        print("  memory and spill limits")
        for name, setting, unit in settings_rows:
            print("    {0:22s} {1} {2}".format(name, setting, unit or ""))

        # Prove the read-only pin is really on before trusting anything below.
        try:
            await session.execute(text("CREATE TEMP TABLE _probe_write_check (x int)"))
            print("\n  WARNING: the read-only pin is NOT active")
        except Exception:
            await session.rollback()
            print("\n  read-only pin: active (a write was refused by the server)")

        facility_id = (
            await session.execute(
                text(
                    "SELECT id FROM facilities WHERE state_silo = :s AND district = :d "
                    "ORDER BY id LIMIT 1"
                ),
                {"s": SANDBOX_STATE, "d": SANDBOX_DISTRICT},
            )
        ).scalar()
        skus = {s.code: s for s in (await session.execute(select(Sku))).scalars().all()}

        async def national_days(session) -> str:
            snaps = await services.get_snapshots(session)
            return "{0:,} facilities scored".format(len(snaps))

        async def facility_panel(session) -> str:
            snaps = await services.get_snapshots(session, [facility_id])
            return "{0} · {1} medicines".format(
                facility_id, len(snaps[0].skus) if snaps else 0
            )

        async def map_summary(session) -> str:
            out = await aggregates.summary(session, None, None)
            return "{0:,} facilities, {1} critical".format(out["facilities"], out["critical"])

        async def state_rollup(session) -> str:
            return "{0} state buckets".format(len(await aggregates.state_rollup(session, None)))

        async def district_rollup(session) -> str:
            rows = await aggregates.district_rollup(session, SANDBOX_STATE, None)
            return "{0} districts in {1}".format(len(rows), SANDBOX_STATE)

        async def facilities_in_view(session) -> str:
            pins = await aggregates.find_facilities(session, bbox=INDIA_BBOX, limit=2500)
            return "{0:,} pins".format(len(pins))

        async def trust_queue(session) -> str:
            rows = await trust.audit_queue(session, state=None, limit=50)
            return "{0} facilities ranked (national)".format(len(rows))

        async def transfer_plan_read_half(session) -> str:
            # generate_plan writes: it deletes open proposals and inserts the
            # new ones. This runs the half that touches the disk — loading
            # every stock position in the state and solving it — and stops
            # before persisting, so the measurement is honest without the
            # write. The solver itself is pure Python on data already in hand.
            nodes = await redistribution.load_state_nodes(session, SANDBOX_STATE)
            rules = redistribution.PlanRules.from_settings()
            proposals = 0
            for code, sku_nodes in nodes.items():
                meta = skus.get(code)
                if meta is None:
                    continue
                plan = redistribution.plan_sku(
                    code, sku_nodes, rules,
                    controlled=meta.is_controlled, cold_chain=meta.cold_chain,
                )
                proposals += len(plan.transfers)
            return "{0} SKUs loaded, {1} transfers solved (not written)".format(
                len(nodes), proposals
            )

        async def federation_inspector(session) -> str:
            run_id = await session.scalar(
                select(FederationRound.run_id)
                .order_by(FederationRound.completed_at.desc())
                .limit(1)
            )
            rows = (
                await session.execute(
                    select(FederationRound)
                    .where(FederationRound.run_id == run_id)
                    .order_by(FederationRound.round_no)
                )
            ).scalars().all()
            return "{0} rounds".format(len(rows))

        paths = [
            ("map summary", map_summary),
            ("state rollup (landing page)", state_rollup),
            ("district rollup, MH", district_rollup),
            ("facilities in view (national bbox)", facilities_in_view),
            ("facility panel, one facility", facility_panel),
            ("trust queue, national", trust_queue),
            ("transfer plan, MH (read half)", transfer_plan_read_half),
            ("federation inspector", federation_inspector),
            # Last on purpose: it is the one that may not finish, and a
            # timeout must not cost the measurements taken before it.
            ("days of stock, every facility", national_days),
        ]

        print("\n  {0:36s} {1:>9s}  {2:>7s}  {3}".format("path", "time", "spill", "result"))
        print("  " + "-" * 76)
        files_before, bytes_before = await _temp(session)
        for label, fn in paths:
            started = time.perf_counter()
            async with Session() as path_session:
                try:
                    detail = await asyncio.wait_for(
                        fn(path_session), timeout=PATH_TIMEOUT_S
                    )
                except asyncio.TimeoutError:
                    detail = "UNFINISHED after {0:.0f}s".format(PATH_TIMEOUT_S)
                except Exception as exc:
                    detail = "FAILED: {0}".format(str(exc).splitlines()[0][:60])
            elapsed = time.perf_counter() - started
            # The statistics collector lags the query it is counting.
            await asyncio.sleep(0.4)
            files_now, bytes_now = await _temp(session)
            spilled = bytes_now - bytes_before
            print(
                "  {0:36s} {1:>8.2f}s  {2:>7s}  {3}".format(
                    label,
                    elapsed,
                    "-" if spilled == 0 else "{0:.1f} MB".format(spilled / 1024 ** 2),
                    detail,
                )
            )
            files_before, bytes_before = files_now, bytes_now

        files_end, bytes_end = await _temp(session)
        print("\n  pg_stat_database totals (cumulative since the stats were reset)")
        print("    temp_files {0:,}   temp_bytes {1:,} ({2:.1f} MB)".format(
            files_end, bytes_end, bytes_end / 1024 ** 2))

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

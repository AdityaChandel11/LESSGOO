"""What the pharmacist's loop costs the database, measured.

`scripts/remote.py` refuses a local host because it is a remote tool. This is
the mirror image: it refuses a *remote* host, because it writes rows in order
to weigh them, and the deployed database is a 1 GB volume under a standing
instruction that bulk writes need Aditya to ask for that specific run.

    python -m scripts.storage_receipt
    python -m scripts.storage_receipt --runs 40
    python -m scripts.storage_receipt --keep      # leave the rows behind

Why it runs the loop many times. A single pass is a bad measurement: Postgres
writes into pages it has already allocated, so one loop reports zero bytes for
most tables and a whole 8 KB page for whichever one happened to fill.
Repeating amortises the page arithmetic, and dividing at the end gives a figure
worth writing down. Dividing a near-empty table's total size by its row count
does not work either, and the attempt is instructive: on a fresh database
`approvals` holds one row in a 48 KB relation, which reports one approval as
costing 48 KB.

Why each run uses a *different* centre. A loop ends by confirming a delivery,
which tops that centre's shelf back up, so the same centre is no longer short
on the next pass and has nothing to request. The script collects the centres
genuinely short of the medicine and gives each one a single loop.

It signs in as the admin, who may act for any facility, rather than as the demo
pharmacist who may act only for their own. The rows written are identical
either way; the route, the permission check and the ledger are the product's.

`pg_total_relation_size` counts indexes and TOAST, which is the right number:
an index page costs the same disk as a heap page.
"""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select, text

from app import services
from app.config import settings
from app.db import SessionLocal
from app.models import Approval, MedicineMovement, StockReading, Transfer

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Every table a judge's pass through the workspace can write to.
# facility_sku_state is here to prove it does not grow: it is updated in place
# rather than appended to, and a size guard should be able to see that.
TABLES = (
    "transfers",
    "approvals",
    "medicine_movements",
    "stock_readings",
    "events",
    "facility_sku_state",
)

SKU = "AMOX"
STATE = "MH"
BASE_URL = "http://receipt.local"


class CannotMeasure(Exception):
    """Raised instead of exiting, so whatever earlier loops created is still
    cleaned up. An exception that skips cleanup leaves drift exactly when it
    matters most."""


def require_local() -> str:
    host = urlsplit(settings.database_url.replace("+asyncpg", "")).hostname or ""
    print("  target host :", host or "(none)")
    if host not in LOCAL_HOSTS:
        raise SystemExit(
            "REFUSING: {0} is not a local host. This script writes rows in order "
            "to measure them, and the deployed database is not somewhere to do "
            "that.".format(host)
        )
    return host


async def measure(session) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for table in TABLES:
        rows = (await session.execute(text("SELECT count(*) FROM " + table))).scalar()
        size = (
            await session.execute(
                text("SELECT pg_total_relation_size(:t)"), {"t": table}
            )
        ).scalar()
        out[table] = (int(rows), int(size))
    return out


async def short_facilities(limit: int) -> list[str]:
    """Centres genuinely below the at-risk line for this medicine, worst first.

    Read straight from the cached position rather than through the solver: this
    only needs candidates, and the API's own donor split decides whether each
    one can actually be served. Bounded by state, SKU and LIMIT.
    """
    async with SessionLocal() as session:
        rows = await session.execute(
            text(
                """
                SELECT s.facility_id
                FROM facility_sku_state s
                JOIN facilities f ON f.id = s.facility_id
                WHERE f.state_silo = :state
                  AND s.sku_code = :sku
                  AND s.daily_burn_rate > 0
                  AND s.qty_on_hand / s.daily_burn_rate < :trigger
                ORDER BY s.qty_on_hand / s.daily_burn_rate
                LIMIT :limit
                """
            ),
            {
                "state": STATE,
                "sku": SKU,
                "trigger": settings.at_risk_days,
                "limit": limit,
            },
        )
        return [r[0] for r in rows.all()]


async def sign_in(http: httpx.AsyncClient, email: str) -> None:
    r = await http.post("/api/auth/demo", json={"email": email})
    if r.status_code != 200:
        raise CannotMeasure(
            "could not sign in as {0}: {1}".format(email, r.status_code)
        )


async def one_loop(http: httpx.AsyncClient, facility_id: str) -> int | None:
    """Request, approve, confirm, for one centre.

    Returns the transfer id, or None when this centre cannot be served right
    now. A transfer that was created but could not be approved is still
    returned, because it still has to be cleaned up.
    """
    supply = (
        await http.get(
            "/api/facilities/{0}/supply".format(facility_id), params={"sku": SKU}
        )
    ).json()
    if not supply.get("donors"):
        return None
    donor = supply["donors"][0]
    qty = min(supply["units_needed"], donor["spare_units"])
    if qty < settings.min_transfer_units:
        return None

    made = await http.post(
        "/api/facilities/{0}/requests".format(facility_id),
        json={"sku_code": SKU, "from_facility": donor["facility_id"], "qty": qty},
    )
    if made.status_code != 201:
        return None
    transfer_id = made.json()["transfer_id"]

    approved = await http.post("/api/transfers/{0}/approve".format(transfer_id))
    if approved.status_code != 200:
        return transfer_id

    async with SessionLocal() as session:
        movement = (
            await session.execute(
                select(MedicineMovement).where(
                    MedicineMovement.transfer_id == transfer_id
                )
            )
        ).scalars().first()
    if movement is not None:
        await http.post(
            "/api/movements/{0}/receipt".format(movement.id),
            json={"qty_received": qty, "via": "form"},
        )
    return transfer_id


async def run_loops(runs: int) -> tuple[list[int], set[str]]:
    from app.main import app

    # Three candidates per loop: some will have no donor within range.
    candidates = await short_facilities(runs * 3)
    if not candidates:
        raise CannotMeasure("no centre in {0} is short of {1}".format(STATE, SKU))

    transfer_ids: list[int] = []
    touched: set[str] = set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=BASE_URL,
        headers={"Origin": BASE_URL},
        timeout=60.0,
    ) as http:
        accounts = {
            row["role"]: row["email"]
            for row in (await http.get("/api/auth/demo-accounts")).json()
        }
        if "admin" not in accounts:
            raise CannotMeasure("no demo admin — run `python -m scripts.users demo`")
        await sign_in(http, accounts["admin"])

        for facility_id in candidates:
            if len(transfer_ids) >= runs:
                break
            transfer_id = await one_loop(http, facility_id)
            if transfer_id is None:
                continue
            transfer_ids.append(transfer_id)
            touched.add(facility_id)
            async with SessionLocal() as session:
                row = await session.get(Transfer, transfer_id)
                if row is not None:
                    touched.add(row.from_facility)

    if not transfer_ids:
        raise CannotMeasure("no centre could be served; nothing was written")
    return transfer_ids, touched


async def clean_up(transfer_ids: list[int], touched: set[str]) -> None:
    """Undo every loop, both halves of each one.

    The two readings a loop writes are keyed differently, and missing that is
    how this script first left drift behind. Approval writes the donor's debit
    with `transfer_id` in its payload; confirming the receipt writes the
    recipient's credit with `batch_id` and `movement_id` and no transfer id at
    all. Deleting on transfer_id alone removes the debit and leaves the credit,
    so the recipient keeps units that came from nowhere and stops looking short.
    """
    ids = [str(i) for i in transfer_ids]
    batch_ids = ["TRF-{0}".format(i) for i in transfer_ids]
    async with SessionLocal() as session:
        await session.execute(
            delete(Approval).where(Approval.transfer_id.in_(transfer_ids))
        )
        await session.execute(
            delete(MedicineMovement).where(
                MedicineMovement.transfer_id.in_(transfer_ids)
            )
        )
        await session.execute(
            delete(StockReading).where(
                StockReading.source == "transfer",
                StockReading.raw_payload["transfer_id"].astext.in_(ids),
            )
        )
        await session.execute(
            delete(StockReading).where(
                StockReading.source == "transfer",
                StockReading.raw_payload["batch_id"].astext.in_(batch_ids),
            )
        )
        await session.execute(delete(Transfer).where(Transfer.id.in_(transfer_ids)))
        await session.commit()
        for facility_id in touched:
            await services.refresh_facility_state(session, facility_id)
        await session.commit()


def report(before: dict, after: dict, runs: int) -> None:
    print()
    print(
        "  {0} full loops measured (request, approve, dispatch, confirm)".format(runs)
    )
    print()
    print(
        "  {0:<22} {1:>10} {2:>12} {3:>13}".format(
            "table", "rows/loop", "bytes/loop", "bytes total"
        )
    )
    print("  " + "-" * 62)
    total_rows = total_bytes = 0.0
    for table in TABLES:
        d_rows = (after[table][0] - before[table][0]) / runs
        d_bytes = (after[table][1] - before[table][1]) / runs
        total_rows += d_rows
        total_bytes += d_bytes
        print(
            "  {0:<22} {1:>10,.1f} {2:>12,.0f} {3:>13,}".format(
                table, d_rows, d_bytes, after[table][1] - before[table][1]
            )
        )
    print("  " + "-" * 62)
    print(
        "  {0:<22} {1:>10,.1f} {2:>12,.0f}".format(
            "one full loop", total_rows, total_bytes
        )
    )
    print()
    cap = settings.max_open_facility_requests_global
    print(
        "  The global cap is {0} open requests. At {1:,.0f} bytes a loop that is"
        " about {2:,.0f} bytes ({3:.2f} MB) of judge-driven growth before the"
        " cap refuses another one.".format(
            cap, total_bytes, total_bytes * cap, (total_bytes * cap) / 1_048_576
        )
    )
    print()
    for line in (
        "  Sizes are pg_total_relation_size, so indexes and TOAST are counted.",
        "  They move between runs with page fill and autovacuum: this is a size,",
        "  not a constant.",
        "",
        "  events self-prune after 2 days (app/events.py), so their share is not",
        "  permanent. Nothing else here expires on its own.",
        "",
        "  facility_sku_state is expected to show zero rows added: it is updated",
        "  in place, never appended to.",
    ):
        print(line)


async def main_async(keep: bool, runs: int) -> None:
    require_local()
    async with SessionLocal() as session:
        before = await measure(session)

    transfer_ids: list[int] = []
    touched: set[str] = set()
    try:
        transfer_ids, touched = await run_loops(runs)
    except CannotMeasure as exc:
        raise SystemExit("cannot measure: {0}".format(exc)) from exc
    finally:
        # Report and clean up whatever actually happened, even if the batch
        # stopped half way. Cleanup that only runs on success is cleanup that
        # leaves drift exactly when it matters.
        if transfer_ids:
            async with SessionLocal() as session:
                after = await measure(session)
            report(before, after, len(transfer_ids))
            if keep:
                print(
                    "\n  --keep: leaving {0} transfer(s) in place.".format(
                        len(transfer_ids)
                    )
                )
            else:
                await clean_up(transfer_ids, touched)
                print(
                    "\n  Cleaned up: removed {0} transfer(s); {1} shelves put "
                    "back.".format(len(transfer_ids), len(touched))
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the rows behind instead of deleting them",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=25,
        help="how many loops to measure over (default 25; one loop is noise)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.keep, max(1, args.runs)))


if __name__ == "__main__":
    main()

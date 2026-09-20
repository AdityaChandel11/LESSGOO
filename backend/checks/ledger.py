"""The two-sided medicine ledger: dispatch, receipt, and the gap between them.

What matters here is that overdue is *derived* rather than stored, that a
confirmed delivery is what actually moves stock onto the shelf, and that a
mismatch between what the warehouse sent and what the facility counted settles
as short or over instead of being quietly averaged away.

Everything this check writes carries a CHK batch id and is deleted again.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app import movements, services
from app.models import Facility, MedicineMovement, StockReading

from .harness import PREFIX, Checker, Report, db

# The schema constrains dispatch_source to warehouse, transfer or seed, so the
# checks use the real warehouse value and are identified by their batch id.
SKU = "ORS"
STATE = "MH"


async def run() -> Report:
    c = Checker("ledger")
    created: list[int] = []
    batches: list[str] = []

    async with db() as session:
        facility = (
            await session.execute(
                select(Facility).where(Facility.state_silo == STATE).order_by(Facility.id).limit(1)
            )
        ).scalars().first()
        if facility is None:
            c.skip("no facility in " + STATE)
            return c.report
        # Held as plain values: a rollback further down expires every ORM object,
        # and reading one back mid-coroutine would attempt IO where it cannot.
        facility_id = facility.id
        state_silo = facility.state_silo

        try:
            now = movements.now_utc()

            # --- overdue is derived, never written -------------------------
            batch_late = "{0}-LATE-{1}".format(PREFIX, int(now.timestamp()))
            late = await movements.record_dispatch(
                session,
                batch_id=batch_late,
                sku_code=SKU,
                from_ref="WH-{0}".format(PREFIX),
                to_facility=facility_id,
                state_silo=state_silo,
                qty=40,
                dispatched_at=now - timedelta(days=6),
                dispatch_source="warehouse",
                transit_hours=24,
            )
            await session.commit()
            created.append(late.id)
            batches.append(batch_late)

            stored = await session.get(MedicineMovement, late.id)
            c.eq("a dispatch is stored as in transit", stored.status, movements.OPEN)
            c.ok(
                "expected arrival is in the past",
                stored.expected_by < now,
                "expected_by {0}".format(stored.expected_by),
            )
            c.eq(
                "the ledger derives overdue from the clock",
                movements.display_status(stored.status, stored.expected_by, now),
                movements.OVERDUE,
            )
            c.eq(
                "and the stored status is still untouched",
                (await session.get(MedicineMovement, late.id)).status,
                movements.OPEN,
            )

            rows = await movements.list_movements(
                session, facility_id=facility_id, view="attention", limit=200
            )
            c.ok(
                "an overdue batch surfaces in the attention view",
                any(r.batch_id == batch_late for r in rows),
                "{0} rows needing attention".format(len(rows)),
            )

            # --- confirming a delivery is what moves the stock -------------
            before = await services.get_snapshots(session, [facility_id])
            on_hand_before = next(
                (s.qty_on_hand for s in before[0].skus if s.sku_code == SKU), 0.0
            )

            batch_ok = "{0}-OK-{1}".format(PREFIX, int(now.timestamp()))
            exact = await movements.record_dispatch(
                session,
                batch_id=batch_ok,
                sku_code=SKU,
                from_ref="WH-{0}".format(PREFIX),
                to_facility=facility_id,
                state_silo=state_silo,
                qty=60,
                dispatch_source="warehouse",
                transit_hours=48,
            )
            await session.commit()
            created.append(exact.id)
            batches.append(batch_ok)

            row, changed = await movements.confirm_receipt(
                session, exact.id, qty_received=60, via="check", by_ref="chk:ledger"
            )
            await session.commit()
            c.eq("an exact receipt settles as received", row.status, movements.RECEIVED)

            reading = (
                await session.execute(
                    select(StockReading)
                    .where(
                        StockReading.facility_id == facility_id,
                        StockReading.sku_code == SKU,
                        StockReading.source == "transfer",
                    )
                    .order_by(StockReading.reported_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            c.ok(
                "confirming writes a stock reading, sourced as a transfer",
                reading is not None and reading.raw_payload.get("batch_id") == batch_ok,
                "latest transfer reading {0}".format(
                    None if reading is None else reading.raw_payload.get("batch_id")
                ),
            )

            after = await services.get_snapshots(session, [facility_id])
            on_hand_after = next((s.qty_on_hand for s in after[0].skus if s.sku_code == SKU), 0.0)
            c.near(
                "the facility's shelf moves by exactly what arrived",
                on_hand_after - on_hand_before,
                60.0,
                0.001,
            )
            c.ok(
                "the change is reported back to the caller",
                isinstance(changed, dict) and bool(changed),
                "keys: {0}".format(sorted(changed) if isinstance(changed, dict) else changed),
            )

            # --- the same batch cannot be confirmed twice ------------------
            try:
                await movements.confirm_receipt(
                    session, exact.id, qty_received=60, via="check", by_ref="chk:ledger"
                )
                c.ok("a settled batch refuses a second confirmation", False, "no error raised")
            except movements.ReceiptError:
                c.ok("a settled batch refuses a second confirmation", True)
            await session.rollback()

            # --- a mismatch settles honestly, in both directions -----------
            for label, sent, got, expect in (
                ("short", 100, 70, movements.SHORT),
                ("over", 100, 130, movements.OVER),
                ("within tolerance", 100, 100.5, movements.RECEIVED),
            ):
                batch = "{0}-{1}-{2}".format(PREFIX, label.split()[0].upper(), int(now.timestamp()))
                m = await movements.record_dispatch(
                    session,
                    batch_id=batch,
                    sku_code=SKU,
                    from_ref="WH-{0}".format(PREFIX),
                    to_facility=facility_id,
                    state_silo=state_silo,
                    qty=sent,
                    dispatch_source="warehouse",
                    transit_hours=48,
                )
                await session.commit()
                created.append(m.id)
                batches.append(batch)
                settled, _ = await movements.confirm_receipt(
                    session, m.id, qty_received=got, via="check", by_ref="chk:ledger"
                )
                await session.commit()
                c.eq(
                    "{0} receipt ({1} sent, {2} counted)".format(label, sent, got),
                    settled.status,
                    expect,
                )

            c.eq(
                "tolerance is a ratio, not a fixed number of units",
                movements.settle_status(Decimal("1000"), Decimal("1005")),
                movements.RECEIVED,
            )

        finally:
            async with db() as cleanup:
                await cleanup.execute(
                    delete(StockReading).where(
                        StockReading.source == "transfer",
                        StockReading.reporter_ref == "chk:ledger",
                    )
                )
                if batches:
                    await cleanup.execute(
                        delete(MedicineMovement).where(MedicineMovement.batch_id.in_(batches))
                    )
                await cleanup.commit()
                left = (
                    await cleanup.execute(
                        select(MedicineMovement).where(MedicineMovement.batch_id.in_(batches or [""]))
                    )
                ).first()
                c.ok("check removed the rows it created", left is None)
                # The shelf goes back to where it started, so running the checks
                # twice cannot inflate anyone's stock.
                await services.refresh_facility_state(cleanup, facility_id)
                await cleanup.commit()

    return c.report

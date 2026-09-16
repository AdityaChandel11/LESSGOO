"""The two-sided medicine movement ledger — spec 26.3.

A stock level is one number, reported by the facility that is judged on it.
A movement is two records of the same event, written at opposite ends: the
warehouse (or our own approved transfer) logs what left, the facility confirms
what arrived. Neither side can settle a discrepancy alone, so a short delivery
or a batch that never arrives surfaces on its own.

Overdue is derived, never stored: a batch is overdue when it is still in
transit past `expected_by`. A stored flag would need a sweep to stay true, and
a sweep that silently stops leaves the ledger quietly wrong — the failure mode
this whole layer exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events, services
from .config import settings
from .models import Facility, MedicineMovement, Sku, StockReading

# Settled states, plus the two derived from the clock.
OPEN = "in_transit"
RECEIVED = "received"
SHORT = "short"
OVER = "over"
CANCELLED = "cancelled"
OVERDUE = "overdue"  # derived: OPEN past expected_by

# A discrepancy below this is a counting difference, not a signal worth an
# officer's attention — half a bottle of syrup on a 500-unit consignment.
DISCREPANCY_TOLERANCE = Decimal("0.01")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def settle_status(dispatched: Decimal, received: Decimal) -> str:
    """Which settled state a confirmed quantity puts the batch into."""
    gap = (received - dispatched) / dispatched
    if abs(gap) <= DISCREPANCY_TOLERANCE:
        return RECEIVED
    return OVER if gap > 0 else SHORT


def display_status(status: str, expected_by: datetime, now: datetime) -> str:
    """The status a reader should see, with the clock applied."""
    if status == OPEN and expected_by < now:
        return OVERDUE
    return status


def needs_attention(status: str) -> bool:
    return status in (OVERDUE, SHORT, OVER)


@dataclass(frozen=True)
class MovementRow:
    id: int
    batch_id: str
    sku_code: str
    sku_name: str
    unit: str
    from_ref: str
    to_facility: str
    facility_name: str
    state_silo: str
    district: str
    qty_dispatched: float
    dispatched_at: datetime
    dispatch_source: str
    transfer_id: int | None
    expected_by: datetime
    qty_received: float | None
    received_at: datetime | None
    received_via: str | None
    status: str
    discrepancy_qty: float | None
    days_outstanding: float | None
    note: str | None


def _row(m: MedicineMovement, f: Facility, sku: Sku, now: datetime) -> MovementRow:
    status = display_status(m.status, m.expected_by, now)
    discrepancy = (
        float(m.qty_received - m.qty_dispatched) if m.qty_received is not None else None
    )
    outstanding = (
        round((now - m.expected_by).total_seconds() / 86400, 1)
        if status == OVERDUE
        else None
    )
    return MovementRow(
        id=m.id,
        batch_id=m.batch_id,
        sku_code=m.sku_code,
        sku_name=sku.name,
        unit=sku.unit,
        from_ref=m.from_ref,
        to_facility=m.to_facility,
        facility_name=f.name,
        state_silo=m.state_silo,
        district=f.district,
        qty_dispatched=float(m.qty_dispatched),
        dispatched_at=m.dispatched_at,
        dispatch_source=m.dispatch_source,
        transfer_id=m.transfer_id,
        expected_by=m.expected_by,
        qty_received=float(m.qty_received) if m.qty_received is not None else None,
        received_at=m.received_at,
        received_via=m.received_via,
        status=status,
        discrepancy_qty=discrepancy,
        days_outstanding=outstanding,
        note=m.note,
    )


# ==================================================================== writes ===


async def record_dispatch(
    session: AsyncSession,
    *,
    batch_id: str,
    sku_code: str,
    from_ref: str,
    to_facility: str,
    state_silo: str,
    qty: Decimal | float,
    dispatched_at: datetime | None = None,
    dispatch_source: str,
    transfer_id: int | None = None,
    transit_hours: float | None = None,
) -> MedicineMovement:
    """Log the dispatch half. Called where the batch leaves, not where it lands."""
    sent = dispatched_at or now_utc()
    hours = settings.receipt_window_hours if transit_hours is None else transit_hours
    movement = MedicineMovement(
        batch_id=batch_id,
        sku_code=sku_code,
        from_ref=from_ref,
        to_facility=to_facility,
        state_silo=state_silo,
        qty_dispatched=Decimal(str(qty)),
        dispatched_at=sent,
        dispatch_source=dispatch_source,
        transfer_id=transfer_id,
        expected_by=sent + timedelta(hours=hours),
        status=OPEN,
    )
    session.add(movement)
    await session.flush()
    return movement


class ReceiptError(Exception):
    """The confirmation cannot be applied as asked."""


async def confirm_receipt(
    session: AsyncSession,
    movement_id: int,
    *,
    qty_received: Decimal | float,
    via: str,
    by_ref: str,
    note: str | None = None,
    received_at: datetime | None = None,
) -> tuple[MovementRow, dict]:
    """Confirm arrival, move the stock, and report what changed.

    The confirmation is what puts the medicine on the shelf: it writes a stock
    reading, so the facility's colour on the national map changes as a direct
    result of someone confirming a delivery.
    """
    movement = await session.get(MedicineMovement, movement_id, with_for_update=True)
    if movement is None:
        raise ReceiptError("Unknown movement")
    if movement.status != OPEN:
        raise ReceiptError(
            f"This batch is already settled as '{movement.status}' and cannot be confirmed twice"
        )
    qty = Decimal(str(qty_received))
    if qty < 0:
        raise ReceiptError("Received quantity cannot be negative")

    facility = await session.get(Facility, movement.to_facility)
    sku = await session.get(Sku, movement.sku_code)
    if facility is None or sku is None:
        raise ReceiptError("Movement references a facility or SKU that no longer exists")

    before = await services.get_snapshots(session, [facility.id])
    status_before = before[0].status if before else services.STATUS_HEALTHY
    on_hand_before = next(
        (
            s.qty_on_hand
            for s in (before[0].skus if before else [])
            if s.sku_code == movement.sku_code
        ),
        0.0,
    )

    when = received_at or now_utc()
    movement.qty_received = qty
    movement.received_at = when
    movement.received_via = via
    movement.received_by_ref = by_ref
    movement.note = note
    movement.status = settle_status(movement.qty_dispatched, qty)

    # Source "transfer": stock that arrived by movement, not consumption. The
    # usage-rate calculation excludes these so a delivery never reads as a
    # spike in demand.
    session.add(
        StockReading(
            facility_id=facility.id,
            sku_code=movement.sku_code,
            qty_on_hand=Decimal(str(on_hand_before)) + qty,
            reported_at=when,
            source="transfer",
            reporter_ref=by_ref,
            confidence=Decimal("1.0"),
            raw_payload={
                "movement_id": movement.id,
                "batch_id": movement.batch_id,
                "qty_received": float(qty),
            },
        )
    )
    await session.flush()
    await services.refresh_facility_state(session, facility.id)

    after = await services.get_snapshots(session, [facility.id])
    status_after = after[0].status if after else services.STATUS_HEALTHY
    row = _row(movement, facility, sku, now_utc())

    await events.record(
        session,
        events.MOVEMENT_RECEIVED,
        {
            "movement_id": movement.id,
            "batch_id": movement.batch_id,
            "facility_id": facility.id,
            "facility_name": facility.name,
            "sku_code": movement.sku_code,
            "sku_name": sku.name,
            "qty_dispatched": float(movement.qty_dispatched),
            "qty_received": float(qty),
            "status": movement.status,
            "received_via": via,
        },
        state_silo=facility.state_silo,
    )
    if status_after != status_before:
        await events.record(
            session,
            events.STATUS_CHANGED,
            {
                "facility_id": facility.id,
                "facility_name": facility.name,
                "from": status_before,
                "to": status_after,
            },
            state_silo=facility.state_silo,
        )

    return row, {
        "status_before": status_before,
        "status_after": status_after,
        "status_changed": status_after != status_before,
        "qty_on_hand": float(Decimal(str(on_hand_before)) + qty),
    }


# ===================================================================== reads ===


def _scope(
    stmt: Select,
    *,
    state: str | None,
    district: str | None,
    facility_id: str | None,
    sku: str | None,
) -> Select:
    if state:
        stmt = stmt.where(MedicineMovement.state_silo == state)
    if district:
        stmt = stmt.where(Facility.district == district)
    if facility_id:
        stmt = stmt.where(MedicineMovement.to_facility == facility_id)
    if sku:
        stmt = stmt.where(MedicineMovement.sku_code == sku)
    return stmt


def _status_filter(stmt: Select, view: str, now: datetime) -> Select:
    """`view` is what the officer asked to see, not a stored column."""
    open_and_late = (MedicineMovement.status == OPEN) & (
        MedicineMovement.expected_by < now
    )
    if view == "attention":
        # The default: everything that is not proceeding normally.
        return stmt.where(
            or_(MedicineMovement.status.in_((SHORT, OVER)), open_and_late)
        )
    if view == OVERDUE:
        return stmt.where(open_and_late)
    if view == OPEN:
        return stmt.where(
            (MedicineMovement.status == OPEN) & (MedicineMovement.expected_by >= now)
        )
    if view in (RECEIVED, SHORT, OVER, CANCELLED):
        return stmt.where(MedicineMovement.status == view)
    return stmt  # "all"


async def list_movements(
    session: AsyncSession,
    *,
    state: str | None = None,
    district: str | None = None,
    facility_id: str | None = None,
    sku: str | None = None,
    view: str = "attention",
    limit: int = 100,
    offset: int = 0,
) -> list[MovementRow]:
    now = now_utc()
    stmt = (
        select(MedicineMovement, Facility, Sku)
        .join(Facility, Facility.id == MedicineMovement.to_facility)
        .join(Sku, Sku.code == MedicineMovement.sku_code)
    )
    stmt = _scope(
        stmt, state=state, district=district, facility_id=facility_id, sku=sku
    )
    stmt = _status_filter(stmt, view, now)
    # Oldest problem first: an overdue batch matters more the longer it is out.
    stmt = stmt.order_by(MedicineMovement.expected_by.asc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).all()
    return [_row(m, f, s, now) for m, f, s in rows]


async def summary(
    session: AsyncSession,
    *,
    state: str | None = None,
    district: str | None = None,
    facility_id: str | None = None,
    sku: str | None = None,
) -> dict:
    """Counts by displayed status, plus the units unaccounted for."""
    now = now_utc()
    stmt = select(
        MedicineMovement.status,
        func.count().label("n"),
        func.sum(
            func.coalesce(MedicineMovement.qty_received, 0) - MedicineMovement.qty_dispatched
        ).label("gap"),
        func.count().filter(
            (MedicineMovement.status == OPEN) & (MedicineMovement.expected_by < now)
        ).label("late"),
    ).join(Facility, Facility.id == MedicineMovement.to_facility)
    stmt = _scope(
        stmt, state=state, district=district, facility_id=facility_id, sku=sku
    ).group_by(MedicineMovement.status)

    counts = {OPEN: 0, RECEIVED: 0, SHORT: 0, OVER: 0, CANCELLED: 0, OVERDUE: 0}
    short_units = 0.0
    for status, n, gap, late in (await session.execute(stmt)).all():
        if status == OPEN:
            counts[OVERDUE] += int(late)
            counts[OPEN] += int(n) - int(late)
        else:
            counts[status] = int(n)
        if status == SHORT and gap is not None:
            short_units = abs(float(gap))
    counts["attention"] = counts[OVERDUE] + counts[SHORT] + counts[OVER]
    counts["total"] = sum(
        counts[k] for k in (OPEN, RECEIVED, SHORT, OVER, CANCELLED, OVERDUE)
    )
    return {"counts": counts, "short_units": round(short_units, 1)}

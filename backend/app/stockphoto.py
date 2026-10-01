"""What one line of a photographed stock document does to the shelf.

Spec v3 §26.3–26.4 and fix list #11. The same number means three different
things on three different documents:

    delivery slip   what arrived — added through the delivery ledger, so it
                    settles the dispatch it belongs to and cannot count twice
    issue record    what left the shelf — subtracted
    stock count     what is on the shelf — set

Reading every line as the shelf's new level turned a slip for 10 tablets into
a shelf of 10, and the forecast then learned 490 tablets of demand that never
happened. Whatever this module cannot place with confidence is held: not
applied, with the reason, so the pharmacist can count the shelf or confirm the
delivery on the Orders tab instead. A held line is not stored anywhere.

Pure on purpose. The endpoint gathers the facts; this decides; the endpoint
writes. Nothing here can touch the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

DELIVERY_SLIP = "delivery_slip"
ISSUE_RECORD = "issue_record"
STOCK_COUNT = "stock_count"
UNKNOWN = "unknown"
DOCUMENT_TYPES = (DELIVERY_SLIP, ISSUE_RECORD, STOCK_COUNT)

# The ways a register or slip writes the units this catalogue counts in. Only
# spelling is forgiven here; a strip of ten is never a tablet.
UNIT_SPELLINGS: dict[str, frozenset[str]] = {
    "tablet": frozenset({"tablet", "tablets", "tab", "tabs", "tbl", "tabl"}),
    "capsule": frozenset({"capsule", "capsules", "cap", "caps"}),
    "sachet": frozenset({"sachet", "sachets", "sach"}),
    "bottle": frozenset({"bottle", "bottles", "btl", "btls"}),
    "blister": frozenset({"blister", "blisters"}),
    "ampoule": frozenset({"ampoule", "ampoules", "ampule", "ampules", "amp", "amps"}),
}
_UNIT_OF = {spelling: unit for unit, spellings in UNIT_SPELLINGS.items() for spelling in spellings}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec")

# Quantities on a slip and in the dispatch ledger are the same number or they
# are not; this only absorbs float noise.
_SAME = 1e-6


@dataclass(frozen=True)
class OpenDelivery:
    """A dispatch to this centre still waiting for its receipt."""

    movement_id: int
    batch_id: str
    qty: float


@dataclass(frozen=True)
class SettledDelivery:
    """A dispatch to this centre whose receipt has been confirmed."""

    batch_id: str
    qty: float
    received_at: datetime


@dataclass(frozen=True)
class Decision:
    action: Literal["receive", "subtract", "set", "hold"]
    qty_after: float | None = None
    movement_id: int | None = None
    reason: str | None = None


def normalise_unit(raw: str | None) -> str | None:
    if raw is None:
        return None
    word = re.sub(r"[^a-z]", "", raw.lower())
    return _UNIT_OF.get(word, word) or None


def _day(d: date | datetime) -> str:
    return "{0} {1}".format(d.day, _MONTHS[d.month - 1])


def _qty(x: float) -> str:
    return "{0:,.0f}".format(x) if float(x).is_integer() else "{0:,}".format(x)


def _norm_batch(raw: str | None) -> str:
    return re.sub(r"\s+", "", raw or "").upper()


def _hold(reason: str) -> Decision:
    return Decision(action="hold", reason=reason)


def decide_line(
    *,
    document_type: str,
    qty: float,
    printed_unit: str | None,
    printed_batch: str | None,
    sku_name: str,
    sku_unit: str,
    on_hand: float,
    last_count_at: datetime | None,
    document_date: date | None,
    today: date,
    confidence: float,
    open_deliveries: list[OpenDelivery],
    settled_deliveries: list[SettledDelivery],
    confidence_floor: float,
    max_age_days: int,
) -> Decision:
    """What this line does to the shelf, or why it does nothing.

    `last_count_at` is the newest reading of this medicine that stated the
    shelf level (anything but a delivery's own ledger row). `open_deliveries`
    and `settled_deliveries` are this centre's dispatches of this medicine.
    """
    units = "{0}s".format(sku_unit)

    if document_type not in DOCUMENT_TYPES:
        return _hold(
            "Could not tell whether this is a delivery slip, an issue record or a stock "
            "count. Nothing changed; retake the photo with the whole page in frame."
        )
    if confidence < confidence_floor:
        return _hold(
            "The reader was unsure of this document (its own estimate: {0:.0f}%). Nothing "
            "changed; retake the photo or enter the count by hand.".format(confidence * 100)
        )
    printed = normalise_unit(printed_unit)
    if printed is not None and printed != normalise_unit(sku_unit):
        return _hold(
            "Written in {0}; this centre counts {1} in {2}. Nothing changed; enter the "
            "count in {2}.".format(printed_unit.strip(), sku_name, units)
        )

    if document_date is not None:
        # Dates are compared in UTC, so a document dated today in India can
        # read as tomorrow for the first five and a half hours of the day.
        if document_date > today + timedelta(days=1):
            return _hold(
                "Dated {0}, which is in the future. Nothing changed; check the date on "
                "the document.".format(_day(document_date))
            )
        if (today - document_date).days > max_age_days:
            return _hold(
                "Dated {0}, older than {1} days. The shelf figure may already include it, "
                "so nothing changed.".format(_day(document_date), max_age_days)
            )
        # A delivery is exempt: confirming an open dispatch is what the Orders
        # form does, and it does not ask when the shelf was last counted.
        if (
            document_type != DELIVERY_SLIP
            and last_count_at is not None
            and document_date < last_count_at.date()
        ):
            what = (
                "applying it would replace a newer figure with an older one"
                if document_type == STOCK_COUNT
                else "that count already includes these issues"
            )
            return _hold(
                "Dated {0}, before this centre's last count of {1} on {2}; {3}. Nothing "
                "changed.".format(_day(document_date), sku_name, _day(last_count_at), what)
            )

    if document_type == STOCK_COUNT:
        return Decision(action="set", qty_after=qty)

    if document_type == ISSUE_RECORD:
        after = on_hand - qty
        if after < 0:
            return _hold(
                "Subtracting {0} from the {1} on record would go below zero. Nothing "
                "changed; count the shelf and enter what is there.".format(_qty(qty), _qty(on_hand))
            )
        return Decision(action="subtract", qty_after=after)

    return _match_delivery(
        qty=qty, printed_batch=printed_batch, sku_name=sku_name, units=units,
        on_hand=on_hand, open_deliveries=open_deliveries,
        settled_deliveries=settled_deliveries,
    )


def _already(settled: SettledDelivery) -> Decision:
    return _hold(
        "Already confirmed on {0}. Not added again.".format(_day(settled.received_at))
    )


def _match_delivery(
    *,
    qty: float,
    printed_batch: str | None,
    sku_name: str,
    units: str,
    on_hand: float,
    open_deliveries: list[OpenDelivery],
    settled_deliveries: list[SettledDelivery],
) -> Decision:
    batch = _norm_batch(printed_batch)
    if batch:
        for d in open_deliveries:
            if _norm_batch(d.batch_id) != batch:
                continue
            if abs(d.qty - qty) > _SAME:
                return _hold(
                    "Batch {0} was dispatched as {1}; the slip reads {2}. Nothing changed; "
                    "confirm what arrived on the Orders tab.".format(d.batch_id, _qty(d.qty), _qty(qty))
                )
            return Decision(action="receive", qty_after=on_hand + qty, movement_id=d.movement_id)
        for s in settled_deliveries:
            if _norm_batch(s.batch_id) == batch:
                return _already(s)
        return _hold(
            "Delivery with no dispatch record: batch {0} is not on its way to this centre. "
            "Nothing changed.".format(printed_batch.strip())
        )

    same = [d for d in open_deliveries if abs(d.qty - qty) <= _SAME]
    if len(same) == 1:
        return Decision(action="receive", qty_after=on_hand + qty, movement_id=same[0].movement_id)
    if len(same) > 1:
        return _hold(
            "{0} deliveries of {1} {2} of {3} are on their way. Nothing changed; confirm "
            "this one on the Orders tab.".format(len(same), _qty(qty), units, sku_name)
        )
    settled = [s for s in settled_deliveries if abs(s.qty - qty) <= _SAME]
    if settled:
        return _already(max(settled, key=lambda s: s.received_at))
    return _hold(
        "Delivery with no dispatch record: nothing on its way to this centre matches {0} "
        "{1} of {2}. Nothing changed.".format(_qty(qty), units, sku_name)
    )

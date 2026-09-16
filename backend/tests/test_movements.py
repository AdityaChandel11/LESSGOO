"""The two-sided ledger's settlement rules — spec 26.3."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import movements

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "dispatched, received, expected",
    [
        (500, 500, movements.RECEIVED),
        (500, 480, movements.SHORT),
        (500, 520, movements.OVER),
        (500, 0, movements.SHORT),          # nothing arrived, but someone confirmed
        (500, 503, movements.RECEIVED),     # within counting tolerance
        (500, 497, movements.RECEIVED),
        (500, 494, movements.SHORT),        # just outside it
    ],
)
def test_settlement_classifies_the_gap(dispatched, received, expected):
    assert (
        movements.settle_status(Decimal(dispatched), Decimal(received)) == expected
    )


def test_overdue_is_derived_from_the_clock_not_stored():
    expected_by = NOW - timedelta(hours=1)
    assert movements.display_status(movements.OPEN, expected_by, NOW) == movements.OVERDUE
    # The same row before its window closes is simply in transit.
    assert (
        movements.display_status(movements.OPEN, NOW + timedelta(hours=1), NOW)
        == movements.OPEN
    )
    # A settled batch never becomes overdue, however long ago it was due.
    assert (
        movements.display_status(movements.RECEIVED, expected_by, NOW)
        == movements.RECEIVED
    )
    assert movements.display_status(movements.SHORT, expected_by, NOW) == movements.SHORT


def test_only_the_exceptions_ask_for_attention():
    assert movements.needs_attention(movements.OVERDUE)
    assert movements.needs_attention(movements.SHORT)
    assert movements.needs_attention(movements.OVER)
    assert not movements.needs_attention(movements.RECEIVED)
    assert not movements.needs_attention(movements.OPEN)
    assert not movements.needs_attention(movements.CANCELLED)

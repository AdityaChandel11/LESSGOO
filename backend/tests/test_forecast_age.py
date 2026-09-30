"""The forecast says how old it is wherever it is used (fix list #81).

A forecast replaces the burn rate only while it is fresh; the card that uses it
now names the day it was published, so "from the shared model's forecast" is
never a claim without a date.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import services
from app.api import SkuStockOut

PUBLISHED = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)


def test_a_fresh_forecast_replaces_the_burn_rate_and_carries_its_date():
    assert services.pick_rate(12.0, (9.5, PUBLISHED)) == (9.5, "federated", PUBLISHED)


def test_without_a_forecast_the_burn_rate_stands_and_has_no_date():
    assert services.pick_rate(12.0, None) == (12.0, "burn_rate", None)


def test_a_zero_forecast_is_not_a_rate():
    assert services.pick_rate(12.0, (0.0, PUBLISHED)) == (12.0, "burn_rate", None)


def test_the_publication_date_reaches_the_api():
    out = SkuStockOut(
        sku_code="ORS", sku_name="ORS", qty_on_hand=10, daily_burn_rate=1, days_of_stock=10,
        rate_source="federated", status="healthy", is_controlled=False, cold_chain=False,
        last_reported_at=None, last_source=None, last_confidence=None,
        forecast_published_at=PUBLISHED,
    )
    assert out.forecast_published_at == PUBLISHED

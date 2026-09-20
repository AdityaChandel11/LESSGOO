"""Report how days-of-stock was computed under this process's FORECAST_MODE.

Settings are read once per process, so the only honest way to check the switch
and its fallback is to ask three separate processes. The federation check runs
this module with different environments and compares the answers.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app import services
from app.config import settings
from app.db import SessionLocal, engine
from app.models import Forecast

SAMPLE = 60


async def main() -> None:
    async with SessionLocal() as session:
        ids = list(
            (
                await session.execute(
                    select(Forecast.facility_id).distinct().order_by(Forecast.facility_id).limit(SAMPLE)
                )
            ).scalars()
        )
        forecast_skus = set(
            (await session.execute(select(Forecast.sku_code).distinct())).scalars()
        )
        out = {
            "mode": settings.forecast_mode,
            "max_age_days": settings.forecast_max_age_days,
            "facilities_sampled": len(ids),
            "forecast_rows_exist": bool(forecast_skus),
            "rate_source": {},
            "forecast_sku_rate_source": {},
            "days_of_stock_present": 0,
            "sku_lines": 0,
        }
        if ids:
            for snap in await services.get_snapshots(session, ids):
                for line in snap.skus:
                    out["sku_lines"] += 1
                    out["rate_source"][line.rate_source] = (
                        out["rate_source"].get(line.rate_source, 0) + 1
                    )
                    if line.days_of_stock is not None:
                        out["days_of_stock_present"] += 1
                    if line.sku_code in forecast_skus:
                        key = line.rate_source
                        out["forecast_sku_rate_source"][key] = (
                            out["forecast_sku_rate_source"].get(key, 0) + 1
                        )
    await engine.dispose()
    print(json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main())

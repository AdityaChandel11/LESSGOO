"""The "Next 14 days" strip — fix list #45 (absorbs #2).

Which district will run short of which medicine, and when: the top district ×
medicine pairs whose centres are projected to run out inside the horizon,
earliest first.

A centre's run-out date is counted from its last count (fix #26's rule): the
quantity counted, divided by a daily rate, added to the day it was counted.
The rate is the shared model's published forecast where one is fresh for that
centre and medicine, otherwise the burn rate the map already stores; where an
outbreak is active, it is the outbreak rate fix #41 computes. Every pair says
which of those its dates rest on. Nothing here is predicted by anything else.

One aggregate over `facility_sku_state` — the map's own table, one row per
centre and medicine — never the reading history, and capped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import outbreak, services, workspace
from .config import settings
from .geo import STATE_BY_CODE

HORIZON_DAYS = 14
DEFAULT_LIMIT = 8
# More district × medicine pairs than this are read, so that outbreak figures
# merged in afterwards cannot push an ordinary pair out of a short list.
READ_LIMIT = 40

Key = tuple[str, str, str]


@dataclass(frozen=True)
class OutbreakNote:
    disease: str
    # "observed" (the district's own rise in use) or "assumption" (the
    # declaring officer's expected surge).
    basis: str
    # When the first centre would run out without the outbreak.
    without_on: date


@dataclass(frozen=True)
class Pair:
    state: str
    district: str
    sku_code: str
    sku_name: str
    # Centres projected to run out inside the horizon.
    centres: int
    first_on: date
    first_centre: str
    # How many of those dates rest on the shared model's forecast.
    by_forecast: int
    outbreak: OutbreakNote | None = None

    @property
    def key(self) -> Key:
        return (self.state, self.district, self.sku_code)

    @property
    def source(self) -> str:
        if self.outbreak is not None:
            return "outbreak"
        if self.by_forecast >= self.centres:
            return "forecast"
        return "mixed" if self.by_forecast > 0 else "burn_rate"


def rank(pairs: list[Pair], limit: int) -> list[Pair]:
    """Earliest run-out first; on the same day, the pair with more centres."""
    return sorted(pairs, key=lambda p: (p.first_on, -p.centres, p.district, p.sku_code))[:limit]


def outbreak_figures(
    *, state: str, district: str, disease: str, warnings: list[dict]
) -> dict[Key, Pair]:
    """One outbreak's per-centre warnings (outbreak.warnings) as one figure
    per medicine: how many centres, and the first of them."""
    out: dict[Key, Pair] = {}
    by_sku: dict[str, list[dict]] = {}
    for w in warnings:
        by_sku.setdefault(w["sku_code"], []).append(w)
    for sku, rows in by_sku.items():
        first = min(rows, key=lambda w: (w["runs_out_on"], w["facility_name"]))
        out[(state, district, sku)] = Pair(
            state=state, district=district, sku_code=sku, sku_name=first["sku_name"],
            centres=len(rows), first_on=first["runs_out_on"],
            first_centre=first["facility_name"], by_forecast=0,
            outbreak=OutbreakNote(disease, first["basis"], first["runs_out_without"]),
        )
    return out


def merge_outbreak(pairs: list[Pair], surged: dict[Key, Pair]) -> list[Pair]:
    """Where an outbreak is active the outbreak rate is the figure: it
    replaces the ordinary one for that district and medicine."""
    merged = {p.key: p for p in pairs}
    merged.update(surged)
    return list(merged.values())


def line(p: Pair, named: bool = True) -> str:
    """One pair in words, the way the strip and a screen reader say it. The
    first centre is named only for a reader who may read its rows (fix #77)."""
    when = workspace.day_words(p.first_on)
    if p.centres == 1:
        body = "{0} runs out on {1}".format(p.first_centre if named else "1 centre", when)
    elif named:
        body = "{0} centres run out within {1} days, the first ({2}) on {3}".format(
            p.centres, HORIZON_DAYS, p.first_centre, when
        )
    else:
        body = "{0} centres run out within {1} days, the first on {2}".format(
            p.centres, HORIZON_DAYS, when
        )
    tail = (
        " ({0} without the {1} outbreak)".format(
            workspace.day_words(p.outbreak.without_on), p.outbreak.disease
        )
        if p.outbreak is not None
        else ""
    )
    return "{0} · {1} · {2}{3}".format(p.district, p.sku_name, body, tail)


# `rate` is the fresh forecast where there is one, else the stored burn rate.
# Run-out is counted from the last count, so a centre nobody has counted for a
# month shows as past its date ("overdue") rather than as fine.
PAIRS_SQL = """
WITH centre AS (
    SELECT f.state_silo, f.district, f.name, s.sku_code,
           fc.facility_id IS NOT NULL AS by_forecast,
           s.last_reported_at + make_interval(
               secs => 86400.0 * s.qty_on_hand
                       / COALESCE(fc.predicted_daily_use, s.daily_burn_rate)
           ) AS run_out
    FROM facility_sku_state s
    JOIN facilities f ON f.id = s.facility_id
    LEFT JOIN forecasts fc
           ON :use_forecast
          AND fc.facility_id = s.facility_id AND fc.sku_code = s.sku_code
          AND fc.computed_at >= :fresh AND fc.predicted_daily_use > 0
          AND f.state_silo = ANY(:model_states) AND s.sku_code = ANY(:model_skus)
    WHERE s.last_reported_at IS NOT NULL
      AND COALESCE(fc.predicted_daily_use, s.daily_burn_rate) > 0
      AND (CAST(:state AS text) IS NULL OR f.state_silo = :state)
      AND (NOT :only_forecast OR fc.facility_id IS NOT NULL)
)
SELECT c.state_silo, c.district, c.sku_code, k.name AS sku_name,
       COUNT(*) AS centres,
       MIN(c.run_out) AS first_at,
       (ARRAY_AGG(c.name ORDER BY c.run_out, c.name))[1] AS first_centre,
       COUNT(*) FILTER (WHERE c.by_forecast) AS by_forecast
FROM centre c
JOIN skus k ON k.code = c.sku_code
WHERE c.run_out > :now AND c.run_out <= :until
GROUP BY c.state_silo, c.district, c.sku_code, k.name
ORDER BY first_at, centres DESC
LIMIT :limit
"""

# Centre × medicine rows whose date has already passed: the shelf may be
# empty, and the instruction is to count it. A number, never a list.
OVERDUE_SQL = """
SELECT COUNT(*)
FROM facility_sku_state s
JOIN facilities f ON f.id = s.facility_id
WHERE s.last_reported_at IS NOT NULL AND s.daily_burn_rate > 0
  AND s.last_reported_at + make_interval(secs => 86400.0 * s.qty_on_hand / s.daily_burn_rate)
      <= :now
  AND (CAST(:state AS text) IS NULL OR f.state_silo = :state)
"""


@dataclass(frozen=True)
class Strip:
    horizon_days: int
    as_of: datetime
    pairs: list[Pair]
    # Centre × medicine counts past their own run-out date.
    counts_overdue: int
    # The newest forecast in use, or None when every date rests on burn rate.
    forecast_published_at: datetime | None
    forecast_max_age_days: float


async def strip(
    session: AsyncSession,
    now: datetime,
    *,
    state: str | None = None,
    only_forecast: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> Strip:
    use_forecast = settings.forecast_mode == "federated"
    fresh = now - timedelta(days=settings.forecast_max_age_days)
    params = {
        "now": now, "until": now + timedelta(days=HORIZON_DAYS), "state": state,
        "use_forecast": use_forecast, "fresh": fresh, "only_forecast": only_forecast,
        # A forecast row outside the model's coverage is not used (fix #55).
        "model_states": list(services.MODEL_STATES), "model_skus": list(services.MODEL_SKUS),
    }
    rows = (await session.execute(text(PAIRS_SQL), {**params, "limit": READ_LIMIT})).all()
    pairs = [
        Pair(
            state=r.state_silo, district=r.district, sku_code=r.sku_code, sku_name=r.sku_name,
            centres=r.centres, first_on=workspace.in_india(r.first_at).date(),
            first_centre=r.first_centre, by_forecast=r.by_forecast,
        )
        for r in rows
    ]

    if not only_forecast:
        surged: dict[Key, Pair] = {}
        for row in await outbreak.active(session, now, state):
            view = await outbreak.evaluate(session, row, now)
            surged.update(
                outbreak_figures(
                    state=row.state_silo, district=row.district,
                    disease=row.disease_category or "",
                    warnings=await outbreak.warnings(session, view, now),
                )
            )
        pairs = merge_outbreak(pairs, surged)

    overdue = await session.scalar(text(OVERDUE_SQL), {"now": now, "state": state})
    published = None
    if use_forecast:
        published = await session.scalar(
            text(
                "SELECT MAX(computed_at) FROM forecasts WHERE computed_at >= :fresh"
            ),
            {"fresh": fresh},
        )
    return Strip(
        horizon_days=HORIZON_DAYS, as_of=now, pairs=rank(pairs, limit),
        counts_overdue=int(overdue or 0), forecast_published_at=published,
        forecast_max_age_days=settings.forecast_max_age_days,
    )


def state_name(code: str) -> str:
    geo = STATE_BY_CODE.get(code)
    return geo.name if geo else code


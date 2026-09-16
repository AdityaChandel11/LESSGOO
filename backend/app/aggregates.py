"""Level-of-detail aggregation for the national map.

The map never ships every facility to the browser. Three zoom tiers:

    zoom < 6      -> one bubble per state      (/map/states)
    6 <= zoom < 8 -> one bubble per district   (/map/districts)
    zoom >= 8     -> individual facilities     (/map/facilities, viewport-bound)

All of these read `facility_sku_state`, never the reading history — that is the
entire reason the snapshot table exists.

When `sku` is given, a facility's status is that commodity's status and
`min_days` is that commodity's days of cover. Otherwise status is the worst
across every commodity the facility stocks.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .geo import STATE_BY_CODE

RANK_TO_STATUS = {3: "critical", 2: "at_risk", 1: "healthy"}
STATUS_TO_RANK = {v: k for k, v in RANK_TO_STATUS.items()}

_RANK = "MAX(CASE s.status WHEN 'critical' THEN 3 WHEN 'at_risk' THEN 2 ELSE 1 END)"


def _facility_cte(sku: str | None, clauses: list[str]) -> str:
    """Per-facility status. Clause strings are fixed fragments with bound
    parameters; no caller-supplied value is ever interpolated into SQL."""
    where_parts = list(clauses)
    if sku:
        where_parts.insert(0, "s.sku_code = :sku")
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return f"""
        SELECT f.id, f.name, f.type, f.state_silo, f.district, f.lat, f.lng,
               {_RANK} AS rnk,
               MIN(s.days_of_stock) AS min_days,
               COUNT(*) FILTER (WHERE s.status = 'critical') AS crit_skus,
               COUNT(*) FILTER (WHERE s.status = 'at_risk')  AS risk_skus
        FROM facilities f
        JOIN facility_sku_state s ON s.facility_id = f.id
        {where}
        GROUP BY f.id
    """


def _params(sku: str | None, **extra: object) -> dict[str, object]:
    params = {k: v for k, v in extra.items() if v is not None}
    if sku:
        params["sku"] = sku
    return params


@dataclass
class Bucket:
    key: str
    label: str
    lat: float
    lng: float
    total: int
    critical: int
    at_risk: int
    healthy: int
    min_days: float | None
    zoom: int
    parent: str | None = None

    @property
    def status(self) -> str:
        if self.critical:
            return "critical"
        if self.at_risk:
            return "at_risk"
        return "healthy"

    @property
    def critical_pct(self) -> float:
        return round(100.0 * self.critical / self.total, 1) if self.total else 0.0


_COUNTS = """
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE rnk = 3) AS critical,
    COUNT(*) FILTER (WHERE rnk = 2) AS at_risk,
    COUNT(*) FILTER (WHERE rnk = 1) AS healthy,
    MIN(min_days) AS min_days
"""


def _f(v: object) -> float | None:
    return float(v) if v is not None else None


async def state_rollup(session: AsyncSession, sku: str | None = None) -> list[Bucket]:
    sql = text(f"""
        WITH fs AS ({_facility_cte(sku, [])})
        SELECT state_silo, {_COUNTS} FROM fs GROUP BY state_silo
    """)
    out: list[Bucket] = []
    for code, total, crit, risk, healthy, min_days in (
        await session.execute(sql, _params(sku))
    ).all():
        geo = STATE_BY_CODE.get(code)
        if geo is None:
            continue
        out.append(
            Bucket(code, geo.name, geo.lat, geo.lng, total, crit, risk, healthy,
                   _f(min_days), geo.zoom)
        )
    # Rank by how many facilities are critical, not by share: share alone lets
    # a 6-facility UT outrank a state with a hundred critical centres.
    out.sort(key=lambda b: (-b.critical, -b.critical_pct))
    return out


async def district_rollup(
    session: AsyncSession, state_code: str | None = None, sku: str | None = None
) -> list[Bucket]:
    clauses = ["f.state_silo = :state"] if state_code else []
    sql = text(f"""
        WITH fs AS ({_facility_cte(sku, clauses)})
        SELECT state_silo, district, {_COUNTS}, AVG(lat) AS lat, AVG(lng) AS lng
        FROM fs GROUP BY state_silo, district
    """)
    rows = (await session.execute(sql, _params(sku, state=state_code))).all()
    out = [
        Bucket(
            key=f"{state}:{district}",
            label=district,
            lat=float(lat),
            lng=float(lng),
            total=total,
            critical=crit,
            at_risk=risk,
            healthy=healthy,
            min_days=_f(min_days),
            zoom=9,
            parent=state,
        )
        for state, district, total, crit, risk, healthy, min_days, lat, lng in rows
    ]
    out.sort(key=lambda b: (-b.critical, -b.critical_pct))
    return out


@dataclass
class FacilityPin:
    id: str
    name: str
    type: str
    district: str
    state_silo: str
    lat: float
    lng: float
    status: str
    min_days: float | None
    critical_skus: int
    at_risk_skus: int


async def find_facilities(
    session: AsyncSession,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    state: str | None = None,
    district: str | None = None,
    sku: str | None = None,
    status: str | None = None,
    limit: int = 1500,
) -> list[FacilityPin]:
    """Facilities in a viewport and/or a state or district, worst first.

    Capped, so an over-wide query degrades to "the most urgent N" rather than
    to a browser stall.
    """
    clauses: list[str] = []
    south = west = north = east = None
    if bbox:
        south, west, north, east = bbox
        clauses.append("f.lat BETWEEN :south AND :north AND f.lng BETWEEN :west AND :east")
    if state:
        clauses.append("f.state_silo = :state")
    if district:
        clauses.append("f.district = :district")

    sql = text(f"""
        WITH fs AS ({_facility_cte(sku, clauses)})
        SELECT id, name, type, district, state_silo, lat, lng,
               rnk, min_days, crit_skus, risk_skus
        FROM fs
        {"WHERE rnk = :rank" if status else ""}
        ORDER BY rnk DESC, min_days ASC NULLS LAST, name
        LIMIT :limit
    """)
    params = _params(
        sku,
        south=south, west=west, north=north, east=east,
        state=state, district=district, limit=limit,
        rank=STATUS_TO_RANK.get(status) if status else None,
    )
    return [
        FacilityPin(
            id=r[0], name=r[1], type=r[2], district=r[3], state_silo=r[4],
            lat=r[5], lng=r[6], status=RANK_TO_STATUS[r[7]],
            min_days=_f(r[8]), critical_skus=r[9], at_risk_skus=r[10],
        )
        for r in (await session.execute(sql, params)).all()
    ]


async def summary(
    session: AsyncSession, sku: str | None = None, state: str | None = None
) -> dict[str, object]:
    clauses = ["f.state_silo = :state"] if state else []
    sql = text(f"""
        WITH fs AS ({_facility_cte(sku, clauses)})
        SELECT {_COUNTS},
               COUNT(DISTINCT state_silo) AS states,
               COUNT(DISTINCT district) AS districts,
               COUNT(*) FILTER (WHERE type = 'CHC') AS chcs
        FROM fs
    """)
    row = (await session.execute(sql, _params(sku, state=state))).one()
    geo = STATE_BY_CODE.get(state) if state else None
    return {
        "facilities": row[0],
        "critical": row[1],
        "at_risk": row[2],
        "healthy": row[3],
        "min_days": _f(row[4]),
        "states": row[5],
        "districts": row[6],
        "chcs": row[7],
        "phcs": row[0] - row[7],
        "sku": sku,
        "state": state,
        "state_name": geo.name if geo else None,
    }

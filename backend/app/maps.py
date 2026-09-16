"""Road distances — the single boundary to Google's Routes API (spec Section 15).

MAPS_MODE=osm     straight-line km x road factor, no network, no key
MAPS_MODE=google  Routes API computeRouteMatrix, cached in route_matrix_cache

Cost discipline, because the Routes API bills per origin-destination element:

  * Only pairs a plan actually proposes are looked up, never every facility
    pair in a state (that would be hundreds of thousands of elements).
  * Every answer is cached permanently. Roads between two health centres do
    not change week to week, so a pair is paid for once.
  * Any failure (no key, quota, timeout, no road found) falls back to the
    straight-line estimate for that pair and is labelled as such, so a plan is
    never blocked by the maps provider and never passes off an estimate as a
    road distance.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import RouteMatrixCache

log = logging.getLogger(__name__)

ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
EARTH_RADIUS_KM = 6371.0088
# computeRouteMatrix allows up to 625 elements per request without traffic;
# 25 destinations per origin request stays far inside that.
MAX_DESTINATIONS_PER_REQUEST = 25
CONCURRENT_REQUESTS = 8
REQUEST_TIMEOUT_S = 10.0
CACHE_WRITE_CHUNK = 1000
# Invalid or wrongly restricted key, Routes API not enabled, or billing off.
KEY_REJECTED_STATUSES = {400, 401, 403}

SOURCE_GOOGLE = "google_routes"
SOURCE_ESTIMATE = "haversine"


@dataclass(frozen=True)
class Point:
    id: str
    lat: float
    lng: float


@dataclass(frozen=True)
class RoadLeg:
    km: float
    minutes: float
    source: str


def estimate(a: Point, b: Point) -> RoadLeg:
    """Straight-line distance x road factor, at an average rural driving speed."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dlat = p2 - p1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    km = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h)) * settings.road_factor
    return RoadLeg(round(km, 1), round(km / settings.avg_speed_kmh * 60, 1), SOURCE_ESTIMATE)


def _parse_duration_s(value: str | None) -> float | None:
    # The API encodes durations as strings like "1234s".
    if not value or not value.endswith("s"):
        return None
    try:
        return float(value[:-1])
    except ValueError:
        return None


def parse_matrix_response(
    elements: list[dict], origin: Point, destinations: list[Point]
) -> dict[tuple[str, str], RoadLeg]:
    """Road legs from a computeRouteMatrix response. Elements with no route,
    or an error status, are simply absent so the caller falls back."""
    legs: dict[tuple[str, str], RoadLeg] = {}
    for el in elements:
        if el.get("status", {}).get("code", 0) not in (0, None):
            continue
        if el.get("condition") != "ROUTE_EXISTS":
            continue
        idx = el.get("destinationIndex")
        seconds = _parse_duration_s(el.get("duration"))
        meters = el.get("distanceMeters")
        if idx is None or seconds is None or meters is None or not 0 <= idx < len(destinations):
            continue
        legs[(origin.id, destinations[idx].id)] = RoadLeg(
            round(meters / 1000, 1), round(seconds / 60, 1), SOURCE_GOOGLE
        )
    return legs


async def _fetch_from_origin(
    client: httpx.AsyncClient, origin: Point, destinations: list[Point]
) -> dict[tuple[str, str], RoadLeg]:
    def waypoint(p: Point) -> dict:
        return {"waypoint": {"location": {"latLng": {"latitude": p.lat, "longitude": p.lng}}}}

    response = await client.post(
        ROUTE_MATRIX_URL,
        headers={
            "X-Goog-Api-Key": settings.google_maps_server_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,status,condition,distanceMeters,duration",
        },
        json={
            "origins": [waypoint(origin)],
            "destinations": [waypoint(d) for d in destinations],
            "travelMode": "DRIVE",
            # Traffic-unaware: a planning distance, not a live ETA, and it is
            # both cheaper and cacheable.
            "routingPreference": "TRAFFIC_UNAWARE",
            "regionCode": "IN",
        },
    )
    response.raise_for_status()
    return parse_matrix_response(response.json(), origin, destinations)


async def road_legs(
    session: AsyncSession,
    pairs: set[tuple[str, str]],
    points: dict[str, Point],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[tuple[str, str], RoadLeg]:
    """Road distance for each (origin_id, destination_id) pair."""
    result: dict[tuple[str, str], RoadLeg] = {}
    if not pairs:
        return result

    if settings.maps_mode != "google" or not settings.google_maps_server_key:
        return {p: estimate(points[p[0]], points[p[1]]) for p in pairs}

    cached = (
        await session.execute(
            select(RouteMatrixCache).where(
                RouteMatrixCache.source == SOURCE_GOOGLE,
                RouteMatrixCache.origin_id.in_({o for o, _ in pairs}),
            )
        )
    ).scalars().all()
    for row in cached:
        key = (row.origin_id, row.dest_id)
        if key in pairs and row.distance_km is not None and row.duration_min is not None:
            result[key] = RoadLeg(float(row.distance_km), float(row.duration_min), SOURCE_GOOGLE)

    missing = pairs - result.keys()
    fetched: dict[tuple[str, str], RoadLeg] = {}
    if missing:
        by_origin: dict[str, list[str]] = defaultdict(list)
        for o, d in sorted(missing):
            by_origin[o].append(d)

        jobs = [
            (points[o], [points[d] for d in dests[i : i + MAX_DESTINATIONS_PER_REQUEST]])
            for o, dests in by_origin.items()
            for i in range(0, len(dests), MAX_DESTINATIONS_PER_REQUEST)
        ]
        limiter = asyncio.Semaphore(CONCURRENT_REQUESTS)
        own_client = client is None
        http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
        # A rejected key or disabled API fails every request the same way, so
        # the first such answer stops the rest instead of sending hundreds.
        rejected: list[int] = []

        async def run(origin: Point, dests: list[Point]) -> None:
            async with limiter:
                if rejected:
                    return
                try:
                    fetched.update(await _fetch_from_origin(http, origin, dests))
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    if code in KEY_REJECTED_STATUSES:
                        if not rejected:
                            log.error(
                                "Routes API refused the server key (HTTP %s); using estimated "
                                "distances. Run `python -m scripts.check_maps` for details.", code,
                            )
                        rejected.append(code)
                    else:
                        log.warning("Routes API lookup failed for %s: HTTP %s", origin.id, code)
                except (httpx.HTTPError, ValueError) as exc:
                    # Logged without the key; the pair falls back below.
                    log.warning("Routes API lookup failed for %s: %s", origin.id, type(exc).__name__)

        try:
            await asyncio.gather(*(run(o, d) for o, d in jobs))
        finally:
            if own_client:
                await http.aclose()

        rows = [
            {
                "origin_id": o,
                "dest_id": d,
                "distance_km": leg.km,
                "duration_min": leg.minutes,
                "source": SOURCE_GOOGLE,
            }
            for (o, d), leg in fetched.items()
        ]
        # Chunked to stay under PostgreSQL's limit on bind parameters.
        for i in range(0, len(rows), CACHE_WRITE_CHUNK):
            stmt = pg_insert(RouteMatrixCache).values(rows[i : i + CACHE_WRITE_CHUNK])
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["origin_id", "dest_id"],
                    set_={
                        "distance_km": stmt.excluded.distance_km,
                        "duration_min": stmt.excluded.duration_min,
                        "source": stmt.excluded.source,
                        "computed_at": stmt.excluded.computed_at,
                    },
                )
            )
        if rows:
            await session.commit()
        result.update(fetched)

    for p in pairs - result.keys():
        result[p] = estimate(points[p[0]], points[p[1]])
    return result

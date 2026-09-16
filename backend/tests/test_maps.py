"""Road distances: parsing Google's response, and falling back honestly."""

import asyncio
import json

import httpx
import pytest

from app import maps
from app.maps import Point, estimate, parse_matrix_response

NASHIK = Point("nashik", 19.9975, 73.7898)
PUNE = Point("pune", 18.5204, 73.8567)
SINNAR = Point("sinnar", 19.8467, 73.9976)


def test_estimate_applies_road_factor_and_is_labelled_as_an_estimate():
    leg = estimate(NASHIK, PUNE)
    # ~164 km straight line x 1.3
    assert 205 < leg.km < 220
    assert leg.source == maps.SOURCE_ESTIMATE
    assert estimate(PUNE, NASHIK).km == leg.km


def test_parses_routes_that_exist_and_skips_the_rest():
    elements = [
        {"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
         "distanceMeters": 210400, "duration": "11520s", "status": {}},
        {"originIndex": 0, "destinationIndex": 1, "condition": "ROUTE_NOT_FOUND", "status": {}},
    ]
    legs = parse_matrix_response(elements, NASHIK, [PUNE, SINNAR])
    assert legs == {("nashik", "pune"): maps.RoadLeg(210.4, 192.0, maps.SOURCE_GOOGLE)}


@pytest.mark.parametrize(
    "element",
    [
        {"destinationIndex": 0, "condition": "ROUTE_EXISTS", "distanceMeters": 1, "duration": "9s",
         "status": {"code": 3, "message": "invalid"}},
        {"destinationIndex": 0, "condition": "ROUTE_EXISTS", "distanceMeters": 1, "duration": "nine"},
        {"destinationIndex": 7, "condition": "ROUTE_EXISTS", "distanceMeters": 1, "duration": "9s"},
        {"destinationIndex": 0, "condition": "ROUTE_EXISTS", "duration": "9s"},
    ],
)
def test_malformed_or_failed_elements_are_ignored(element):
    assert parse_matrix_response([element], NASHIK, [PUNE]) == {}


def test_request_sends_key_in_header_and_asks_only_for_needed_fields(monkeypatch):
    monkeypatch.setattr(maps.settings, "google_maps_server_key", "test-server-key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[
            {"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS",
             "distanceMeters": 38200, "duration": "2700s"},
        ])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await maps._fetch_from_origin(client, NASHIK, [SINNAR])

    legs = asyncio.run(run())
    assert legs[("nashik", "sinnar")].km == 38.2
    # The key travels in a header, never in the URL where it would be logged.
    assert "test-server-key" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "test-server-key"
    assert "distanceMeters" in seen["headers"]["x-goog-fieldmask"]
    assert seen["body"]["travelMode"] == "DRIVE"
    assert seen["body"]["routingPreference"] == "TRAFFIC_UNAWARE"


def test_without_google_mode_no_network_or_database_is_touched(monkeypatch):
    monkeypatch.setattr(maps.settings, "maps_mode", "osm")
    pairs = {("nashik", "pune"), ("nashik", "sinnar")}
    points = {p.id: p for p in (NASHIK, PUNE, SINNAR)}
    # session=None: any attempt to use the database would raise.
    legs = asyncio.run(maps.road_legs(None, pairs, points))  # type: ignore[arg-type]
    assert set(legs) == pairs
    assert all(leg.source == maps.SOURCE_ESTIMATE for leg in legs.values())


class _NoCacheSession:
    """Stands in for the database: an empty route cache, and no writes expected."""

    async def execute(self, _stmt):
        class _Result:
            def scalars(self):
                return self

            def all(self):
                return []

        return _Result()

    async def commit(self):
        raise AssertionError("nothing should be cached when every lookup failed")


def test_rejected_key_stops_further_requests_and_falls_back(monkeypatch):
    monkeypatch.setattr(maps.settings, "maps_mode", "google")
    monkeypatch.setattr(maps.settings, "google_maps_server_key", "revoked-key")
    origins = [Point(f"donor{i}", 19.0 + i / 100, 73.0) for i in range(60)]
    points = {p.id: p for p in (*origins, PUNE)}
    pairs = {(o.id, "pune") for o in origins}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, json={"error": {"message": "API key not valid"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await maps.road_legs(_NoCacheSession(), pairs, points, client=client)  # type: ignore[arg-type]

    legs = asyncio.run(run())
    # At most the requests already in flight when the first refusal arrived.
    assert len(calls) <= maps.CONCURRENT_REQUESTS < len(origins)
    assert set(legs) == pairs
    assert all(leg.source == maps.SOURCE_ESTIMATE for leg in legs.values())

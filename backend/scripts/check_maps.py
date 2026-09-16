"""Check the Google Maps keys before relying on them.

    python -m scripts.check_maps
    python -m scripts.check_maps --referer https://your-site.example/

Makes three small calls and reports each:
  1. Routes API road distance between two real district towns (server key)
  2. Map Tiles API session (browser key, sent with the site's Referer, because
     a browser key is restricted to your site's domains)
  3. One map tile from that session
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from app import maps
from app.config import settings

NASHIK = maps.Point("nashik", 19.9975, 73.7898)
PUNE = maps.Point("pune", 18.5204, 73.8567)


def report(ok: bool, label: str, detail: str) -> bool:
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}: {detail}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--referer", default="http://localhost:8080/")
    args = parser.parse_args()

    print(f"MAPS_MODE={settings.maps_mode}")
    results = []

    async with httpx.AsyncClient(timeout=15) as client:
        if not settings.google_maps_server_key:
            results.append(report(False, "Routes API", "GOOGLE_MAPS_SERVER_KEY is not set"))
        else:
            try:
                legs = await maps._fetch_from_origin(client, NASHIK, [PUNE])
                leg = legs.get(("nashik", "pune"))
                straight = maps.estimate(NASHIK, PUNE)
                results.append(report(
                    leg is not None, "Routes API",
                    f"Nashik to Pune {leg.km} km by road, {leg.minutes:.0f} min "
                    f"(estimate was {straight.km} km)" if leg else "no route returned",
                ))
            except httpx.HTTPStatusError as exc:
                detail = exc.response.json().get("error", {}).get("message", exc.response.text[:200])
                results.append(report(False, "Routes API", f"HTTP {exc.response.status_code}: {detail}"))

        if not settings.google_maps_browser_key:
            results.append(report(False, "Map Tiles API", "GOOGLE_MAPS_BROWSER_KEY is not set"))
        else:
            key = settings.google_maps_browser_key
            r = await client.post(
                f"https://tile.googleapis.com/v1/createSession?key={key}",
                headers={"Referer": args.referer, "Content-Type": "application/json"},
                json={"mapType": "roadmap", "language": "en-IN", "region": "IN"},
            )
            if r.status_code != 200:
                msg = r.json().get("error", {}).get("message", r.text[:200])
                results.append(report(False, "Map Tiles session", f"HTTP {r.status_code}: {msg}"))
            else:
                session = r.json()["session"]
                results.append(report(True, "Map Tiles session", "created"))
                t = await client.get(
                    f"https://tile.googleapis.com/v1/2dtiles/5/22/14?session={session}&key={key}",
                    headers={"Referer": args.referer},
                )
                results.append(report(
                    t.status_code == 200 and t.headers.get("content-type", "").startswith("image/"),
                    "Map tile", f"HTTP {t.status_code}, {t.headers.get('content-type')}",
                ))

    ok = all(results)
    print("\nAll good. Set MAPS_MODE=google to use them." if ok else "\nFix the failures above first.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

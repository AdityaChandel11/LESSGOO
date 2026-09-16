/**
 * Google Map Tiles API — Google's supported way to show its basemap inside a
 * third-party map library. The map, rollup rings, routes and zoom tiers all
 * stay as they are; only the background imagery comes from Google.
 *
 * Tile styling is done through the API's own `styles` option rather than CSS
 * filters, so the imagery is displayed as Google renders it.
 */

const TILE_API = "https://tile.googleapis.com";

export interface TileSession {
  session: string;
  expiry: number;
}

export async function createTileSession(key: string): Promise<TileSession> {
  const res = await fetch(`${TILE_API}/v1/createSession?key=${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mapType: "roadmap",
      language: "en-IN",
      region: "IN",
      // Muted basemap so facility status colours carry the meaning.
      styles: [
        { stylers: [{ saturation: -80 }, { lightness: 10 }] },
        { featureType: "poi", stylers: [{ visibility: "off" }] },
        { featureType: "transit", stylers: [{ visibility: "off" }] },
      ],
    }),
  });
  if (!res.ok) throw new Error(`Map Tiles session failed (${res.status})`);
  const body = (await res.json()) as { session: string; expiry: string };
  return { session: body.session, expiry: Number(body.expiry) * 1000 };
}

export function tileUrl(key: string, s: TileSession): string {
  return `${TILE_API}/v1/2dtiles/{z}/{x}/{y}?session=${encodeURIComponent(s.session)}&key=${encodeURIComponent(key)}`;
}

/** The copyright text Google requires for the area currently on screen. */
export async function viewportAttribution(
  key: string,
  s: TileSession,
  view: { zoom: number; north: number; south: number; east: number; west: number },
): Promise<string> {
  const q = new URLSearchParams({
    session: s.session,
    key,
    zoom: String(Math.round(view.zoom)),
    north: view.north.toFixed(5),
    south: view.south.toFixed(5),
    east: view.east.toFixed(5),
    west: view.west.toFixed(5),
  });
  const res = await fetch(`${TILE_API}/tile/v1/viewport?${q}`);
  if (!res.ok) return "Map data ©Google";
  const body = (await res.json()) as { copyright?: string };
  return body.copyright || "Map data ©Google";
}

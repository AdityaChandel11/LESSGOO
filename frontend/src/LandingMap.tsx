import L from "leaflet";
import { useEffect, useRef } from "react";

import { type Bucket, STATUS_COLOR, bucketSeverity } from "./api";

/**
 * The national map as a hero, for visitors who have not signed in.
 *
 * Deliberately not the real NationalMap. That one zooms into district rollups
 * and individual facility pins, both of which stay behind a session — pointing
 * it at a logged-out visitor would only produce 401s. This draws the one tier
 * that is public: a dot per state and union territory, sized by how many
 * health centres it holds and coloured by how many of those are short.
 *
 * Fixed at the national view for the same reason, so there is nothing to zoom
 * into that would fail. Panning and zooming are off, which also keeps the
 * page from swallowing a scroll gesture on the way down to the rest of it.
 *
 * OpenStreetMap tiles, not the Google Map Tiles basemap the signed-in map
 * uses. This page is public and gets crawled; a tile session per visit would
 * spend a metered quota on robots, and Section 1.3 requires every page to
 * render with the credentials blank anyway.
 */

const INDIA = L.latLngBounds([6.4, 67.8], [36.2, 97.6]);
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/** Area, not radius, should track the count, so the square root does the work. */
function radiusFor(total: number): number {
  return Math.min(Math.max(4, 3.5 + Math.sqrt(total) * 0.55), 15);
}

function describe(states: Bucket[]): string {
  if (!states.length) return "Map of India";
  const short = states.filter((s) => bucketSeverity(s) !== "healthy").length;
  return (
    `Map of India with one dot per state and union territory, sized by the number of ` +
    `health centres. ${short} of ${states.length} are carrying facilities that are ` +
    `short of stock.`
  );
}

export default function LandingMap({ states }: { states: Bucket[] }) {
  const host = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!host.current || mapRef.current) return;
    const map = L.map(host.current, {
      zoomControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      touchZoom: false,
      keyboard: false,
      attributionControl: true,
      // Leaflet otherwise snaps to whole zoom levels, which on a container
      // this shape leaves India small in a field of neighbouring countries.
      // Nothing here zooms interactively, so a fractional level costs nothing.
      zoomSnap: 0,
    });
    map.attributionControl.setPrefix(false);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 7,
      className: "basemap",
      attribution: OSM_ATTRIBUTION,
    }).addTo(map);
    map.fitBounds(INDIA, { padding: [6, 6] });
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    // The hero is sized by the page around it, which settles after Leaflet has
    // already measured the container once.
    const observer = new ResizeObserver(() => {
      map.invalidateSize({ animate: false });
      map.fitBounds(INDIA, { padding: [6, 6] });
    });
    observer.observe(host.current);

    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.clearLayers();
    for (const s of states) {
      const status = bucketSeverity(s);
      L.circleMarker([s.lat, s.lng], {
        radius: radiusFor(s.total),
        color: "#ffffff",
        weight: 1.5,
        fillColor: STATUS_COLOR[status],
        fillOpacity: 0.85,
        interactive: false,
      }).addTo(layer);
    }
  }, [states]);

  // India is roughly as tall as it is wide, so a squat container fills its
  // spare width with neighbouring countries. The heights below track that.
  return (
    <div
      ref={host}
      role="img"
      aria-label={describe(states)}
      className="h-[330px] w-full rounded-lg border border-line bg-panel sm:h-[440px] lg:h-[460px]"
    />
  );
}

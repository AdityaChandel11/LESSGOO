import L from "leaflet";
import { useEffect, useRef } from "react";

import { createTileSession, tileUrl, viewportAttribution } from "./googleTiles";

import {
  type Bucket,
  type Pin,
  STATUS_COLOR,
  STATUS_LABEL,
  api,
  bucketSeverity,
  formatDays,
} from "./api";

/**
 * Zoom tiers. The map never draws every facility at once:
 *   state    — one donut per state, zoomed out over India
 *   district — one donut per district
 *   facility — individual PHC/CHC dots, fetched for the viewport only
 */
export type Tier = "state" | "district" | "facility";
export const DISTRICT_ZOOM = 6;
export const FACILITY_ZOOM = 8;
export const tierFor = (z: number): Tier =>
  z < DISTRICT_ZOOM ? "state" : z < FACILITY_ZOOM ? "district" : "facility";

export interface ViewInfo {
  lat: number;
  lng: number;
  zoom: number;
  tier: Tier;
  /** The state holding most facilities in view, once zoomed past national. */
  focusState: string | null;
  pinsInView: number;
  /** False while a zoom/fly animation is still running. flyTo arcs outward
   *  mid-flight, so transient tiers must not drive decisions like clearing
   *  a selection. */
  settled: boolean;
}

export interface FlyTarget {
  lat: number;
  lng: number;
  zoom: number;
  nonce: number;
}

/** One vehicle trip between two facilities, possibly carrying several medicines. */
export interface RouteLine {
  id: string;
  from: [number, number];
  to: [number, number];
  status: "proposed" | "approved" | "mixed";
  urgent: boolean;
  label: string;
}

interface Props {
  sku: string | null;
  refreshKey: number;
  selectedFacilityId: string | null;
  pulse: { id: string; nonce: number } | null;
  flyTarget: FlyTarget | null;
  routes?: RouteLine[];
  highlightRouteId?: string | null;
  /** Where the map opens, e.g. restored from a shared link. Read once. */
  initialView?: { lat: number; lng: number; zoom: number } | null;
  /** Null until runtime config has loaded. */
  basemap?: { mode: "osm" | "google"; key: string } | null;
  onBasemapFallback?: () => void;
  onView: (v: ViewInfo) => void;
  onSelectFacility: (pin: Pin) => void;
  onSelectRoute?: (id: string) => void;
}

const ROUTE_COLOR = { proposed: "#0b3d5c", approved: "#1b9150", mixed: "#0b3d5c" } as const;
// Past this many trips, per-route arrowheads become noise; only the highlighted
// route keeps one.
const MAX_ARROWS = 120;

const INDIA_BOUNDS = L.latLngBounds([4.5, 64.0], [38.5, 101.0]);

/**
 * Fly, unless the user has asked their OS to reduce motion — then jump. Also
 * jump while the map has no size (a collapsed or hidden layout): Leaflet's
 * flight path divides by the container size and would produce NaN positions.
 */
function goTo(map: L.Map, lat: number, lng: number, zoom: number) {
  const size = map.getSize();
  if (size.x === 0 || size.y === 0 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    map.setView([lat, lng], zoom, { animate: false });
  } else {
    map.flyTo([lat, lng], zoom, { duration: 0.9 });
  }
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** A donut showing the share of facilities in each condition, with the
 *  critical count at its centre. Proportions read at a glance; a single flat
 *  colour would hide how much of a state is actually in trouble. */
function donutIcon(b: Bucket, kind: "state" | "district"): L.DivIcon {
  const isState = kind === "state";
  const size = Math.round(
    Math.min(isState ? 76 : 56, (isState ? 30 : 22) + Math.sqrt(b.total) * (isState ? 2.2 : 3)),
  );
  const cx = size / 2;
  const stroke = isState ? 7 : 5.5;
  const r = cx - stroke / 2 - 1;
  const circ = 2 * Math.PI * r;

  let offset = 0;
  const arcs = (
    [
      [b.critical, STATUS_COLOR.critical],
      [b.at_risk, STATUS_COLOR.at_risk],
      [b.healthy, STATUS_COLOR.healthy],
    ] as const
  )
    .filter(([n]) => n > 0)
    .map(([n, color]) => {
      const len = (circ * n) / b.total;
      const arc = `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${color}"
        stroke-width="${stroke}" stroke-dasharray="${len} ${circ - len}"
        stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cx})"/>`;
      offset += len;
      return arc;
    })
    .join("");

  const sev = bucketSeverity(b);
  const html = `
    <div class="donut donut-${kind}" data-sev="${sev}">
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
        <circle cx="${cx}" cy="${cx}" r="${r}" fill="#ffffff"/>
        ${arcs}
        <text x="${cx}" y="${cx}" text-anchor="middle" dominant-baseline="central"
          class="donut-count" fill="${b.critical ? STATUS_COLOR.critical : "#3f4750"}">${b.critical}</text>
      </svg>
      <span class="donut-label">${esc(b.label)}</span>
    </div>`;

  return L.divIcon({
    html,
    className: "donut-host",
    iconSize: [size, size],
    iconAnchor: [cx, cx],
  });
}

function bucketTooltip(b: Bucket, skuLabel: string): string {
  return `
    <div class="tt">
      <div class="tt-title">${esc(b.label)}</div>
      <div class="tt-sub">${b.total} facilities · ${esc(skuLabel)}</div>
      <div class="tt-row"><i style="background:${STATUS_COLOR.critical}"></i>${b.critical} critical</div>
      <div class="tt-row"><i style="background:${STATUS_COLOR.at_risk}"></i>${b.at_risk} at risk</div>
      <div class="tt-row"><i style="background:${STATUS_COLOR.healthy}"></i>${b.healthy} adequate</div>
    </div>`;
}

function pinTooltip(p: Pin, skuLabel: string): string {
  return `
    <div class="tt">
      <div class="tt-title">${esc(p.name)}</div>
      <div class="tt-sub">${esc(p.type)} · ${esc(p.district)}</div>
      <div class="tt-row"><i style="background:${STATUS_COLOR[p.status]}"></i>${STATUS_LABEL[p.status]}
        · ${formatDays(p.min_days)} ${esc(skuLabel === "all medicines" ? "lowest cover" : "of " + skuLabel)}</div>
    </div>`;
}

export default function NationalMap({
  sku,
  refreshKey,
  selectedFacilityId,
  pulse,
  flyTarget,
  routes = [],
  highlightRouteId = null,
  initialView = null,
  basemap = null,
  onBasemapFallback,
  onView,
  onSelectFacility,
  onSelectRoute,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const routesLayerRef = useRef<L.LayerGroup | null>(null);
  const routesRendererRef = useRef<L.Canvas | null>(null);

  const statesRef = useRef<Bucket[]>([]);
  const districtsRef = useRef<Bucket[]>([]);
  const pinsRef = useRef<Pin[]>([]);
  const pinMarkersRef = useRef<Map<string, L.CircleMarker>>(new Map());
  const abortRef = useRef<AbortController | null>(null);
  const moveTimerRef = useRef<number | null>(null);
  // True from movestart until movement has been quiet for a moment. Anything
  // that reports a "settled" view must check this, or a data refresh landing
  // mid-flight reports whatever transient zoom the flyTo arc is passing.
  const movingRef = useRef(false);

  // Leaflet handlers are registered once, so they read live props via refs
  // rather than capturing stale ones.
  const props = useRef({
    sku, selectedFacilityId, onView, onSelectFacility, routes, highlightRouteId, onSelectRoute,
    onBasemapFallback,
  });
  props.current = {
    sku, selectedFacilityId, onView, onSelectFacility, routes, highlightRouteId, onSelectRoute,
    onBasemapFallback,
  };

  const skuLabel = () => props.current.sku ?? "all medicines";

  /* ---------------------------------------------------------- drawing --- */

  const draw = () => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();
    pinMarkersRef.current.clear();

    const tier = tierFor(map.getZoom());

    if (tier === "state") {
      for (const b of statesRef.current) {
        L.marker([b.lat, b.lng], { icon: donutIcon(b, "state"), riseOnHover: true })
          .bindTooltip(bucketTooltip(b, skuLabel()), { direction: "top", offset: [0, -30] })
          .on("click", () => goTo(map, b.lat, b.lng, Math.max(b.zoom, DISTRICT_ZOOM + 0.5)))
          .addTo(layer);
      }
      return;
    }

    if (tier === "district") {
      for (const b of districtsRef.current) {
        L.marker([b.lat, b.lng], { icon: donutIcon(b, "district"), riseOnHover: true })
          .bindTooltip(bucketTooltip(b, skuLabel()), { direction: "top", offset: [0, -22] })
          .on("click", () => goTo(map, b.lat, b.lng, FACILITY_ZOOM + 1))
          .addTo(layer);
      }
      return;
    }

    const selected = props.current.selectedFacilityId;
    // Draw adequate first and critical last, so trouble sits on top.
    const ordered = [...pinsRef.current].sort(
      (a, b) => rank(a.status) - rank(b.status),
    );
    for (const p of ordered) {
      const isSel = p.id === selected;
      const m = L.circleMarker([p.lat, p.lng], {
        radius: p.type === "CHC" ? 7 : 5,
        color: isSel ? "#0f172a" : "#ffffff",
        weight: isSel ? 3 : 1.5,
        fillColor: STATUS_COLOR[p.status],
        fillOpacity: 1,
      })
        .bindTooltip(pinTooltip(p, skuLabel()), { direction: "top", offset: [0, -6] })
        .on("click", () => props.current.onSelectFacility(p))
        .addTo(layer);
      pinMarkersRef.current.set(p.id, m);
    }
  };

  const drawRoutes = () => {
    const map = mapRef.current;
    const layer = routesLayerRef.current;
    const renderer = routesRendererRef.current;
    if (!map || !layer || !renderer) return;
    layer.clearLayers();
    // Trips are a state-level picture; over all of India they are clutter.
    if (tierFor(map.getZoom()) === "state") return;

    const { routes: lines, highlightRouteId: hi } = props.current;
    const showArrows = lines.length <= MAX_ARROWS;
    // Highlighted route last, so it paints over the others.
    const ordered = [...lines].sort((a, b) => Number(a.id === hi) - Number(b.id === hi));

    for (const r of ordered) {
      const isHi = r.id === hi;
      const color = ROUTE_COLOR[r.status];
      if (isHi) {
        L.polyline([r.from, r.to], {
          renderer, pane: "routes", color: "#ffffff", weight: 9, opacity: 0.9, interactive: false,
        }).addTo(layer);
      }
      L.polyline([r.from, r.to], {
        renderer,
        pane: "routes",
        color,
        weight: isHi ? 4.5 : r.urgent ? 2.2 : 1.6,
        opacity: isHi ? 1 : r.status === "approved" ? 0.85 : 0.55,
        dashArray: r.status === "approved" ? undefined : "6 5",
      })
        .bindTooltip(`<div class="tt"><div class="tt-title">${esc(r.label)}</div></div>`, {
          sticky: true,
        })
        .on("click", () => props.current.onSelectRoute?.(r.id))
        .addTo(layer);

      if (showArrows || isHi) {
        const a = map.latLngToLayerPoint(r.from);
        const b = map.latLngToLayerPoint(r.to);
        const deg = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
        const at: [number, number] = [
          r.from[0] + (r.to[0] - r.from[0]) * 0.62,
          r.from[1] + (r.to[1] - r.from[1]) * 0.62,
        ];
        const size = isHi ? 18 : 12;
        L.marker(at, {
          pane: "routeArrows",
          interactive: false,
          icon: L.divIcon({
            className: "route-arrow",
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
            html: `<svg width="${size}" height="${size}" viewBox="0 0 12 12" style="transform:rotate(${deg}deg)">
              <path d="M2 1.5 L11 6 L2 10.5 Z" fill="${color}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></svg>`,
          }),
        }).addTo(layer);
      }
    }
  };

  const emitView = (settled: boolean) => {
    const map = mapRef.current;
    if (!map) return;
    const zoom = map.getZoom();
    const tier = tierFor(zoom);
    let focusState: string | null = null;

    if (tier !== "state") {
      const bounds = map.getBounds();
      const totals = new Map<string, number>();
      for (const d of districtsRef.current) {
        if (d.parent && bounds.contains([d.lat, d.lng])) {
          totals.set(d.parent, (totals.get(d.parent) ?? 0) + d.total);
        }
      }
      if (totals.size) {
        focusState = [...totals.entries()].sort((a, b) => b[1] - a[1])[0][0];
      } else {
        // Zoomed into a gap between district anchors: fall back to the
        // nearest state centre so the panel never goes blank mid-pan.
        const c = map.getCenter();
        let best: Bucket | null = null;
        let bestD = Infinity;
        for (const s of statesRef.current) {
          const dist = (s.lat - c.lat) ** 2 + (s.lng - c.lng) ** 2;
          if (dist < bestD) {
            bestD = dist;
            best = s;
          }
        }
        focusState = best?.key ?? null;
      }
    }

    const center = map.getCenter();
    props.current.onView({
      lat: center.lat,
      lng: center.lng,
      zoom,
      tier,
      focusState,
      pinsInView: tier === "facility" ? pinsRef.current.length : 0,
      settled,
    });
  };

  const loadPins = async () => {
    const map = mapRef.current;
    if (!map || tierFor(map.getZoom()) !== "facility") {
      pinsRef.current = [];
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const b = map.getBounds().pad(0.25);
    try {
      pinsRef.current = await api.pinsInView(
        { south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() },
        props.current.sku,
        ctrl.signal,
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      throw e;
    }
  };

  const loadAggregates = async () => {
    const s = props.current.sku;
    const [states, districts] = await Promise.all([api.states(s), api.districts(s)]);
    statesRef.current = states;
    districtsRef.current = districts;
  };

  const refreshAll = async () => {
    await loadAggregates();
    await loadPins();
    draw();
    // If the map is mid-flight, the moveend settle will report the view.
    if (!movingRef.current) emitView(true);
  };

  /* ------------------------------------------------------------ setup --- */

  useEffect(() => {
    if (mapRef.current || !hostRef.current) return;
    const map = L.map(hostRef.current, {
      center: initialView ? [initialView.lat, initialView.lng] : [22.8, 81.5],
      zoom: initialView?.zoom ?? 5,
      minZoom: 4,
      maxZoom: 16,
      zoomSnap: 0.5,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 90,
      preferCanvas: true,
      maxBounds: INDIA_BOUNDS,
      maxBoundsViscosity: 0.8,
      zoomControl: false,
      attributionControl: true,
    });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    map.attributionControl.setPrefix(false);
    // The basemap itself is added by the basemap effect below, once the
    // runtime config says whether it is Google or OpenStreetMap.

    // Routes get their own panes: lines below the facility dots, arrowheads
    // just above the lines, so a dense plan never hides a facility.
    map.createPane("routes").style.zIndex = "390";
    map.createPane("routeArrows").style.zIndex = "395";
    routesRendererRef.current = L.canvas({ pane: "routes", padding: 0.5 });
    routesLayerRef.current = L.layerGroup().addTo(map);

    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    // Tier switches happen on every zoom frame, not at the end of the
    // animation — otherwise a fly-out shows stale facility dots for a second
    // or two and reads as broken.
    let lastTier = tierFor(map.getZoom());
    map.on("zoom", () => {
      const tier = tierFor(map.getZoom());
      if (tier === lastTier) return;
      lastTier = tier;
      if (tier !== "facility") pinsRef.current = [];
      draw();
      drawRoutes();
      emitView(false);
    });
    // Arrowhead angles are computed in screen space, so they need recomputing
    // once each zoom lands.
    map.on("zoomend", drawRoutes);

    // Viewport-bound work (loading facility pins, recomputing which state is
    // in focus) waits until movement has genuinely stopped.
    const cancelSettle = () => {
      if (moveTimerRef.current) {
        window.clearTimeout(moveTimerRef.current);
        moveTimerRef.current = null;
      }
    };
    let moveGen = 0;
    map.on("movestart zoomstart", () => {
      moveGen += 1;
      movingRef.current = true;
      cancelSettle();
    });
    map.on("move zoom", cancelSettle);
    map.on("moveend", () => {
      cancelSettle();
      const gen = moveGen;
      moveTimerRef.current = window.setTimeout(async () => {
        moveTimerRef.current = null;
        if (tierFor(map.getZoom()) === "facility") {
          await loadPins();
          // A new movement began while pins were loading; its own moveend
          // will settle, so this stale one must not report anything.
          if (gen !== moveGen) return;
          draw();
        }
        movingRef.current = false;
        emitView(true);
      }, 180);
    });

    const pinMarkers = pinMarkersRef.current;
    return () => {
      if (moveTimerRef.current) window.clearTimeout(moveTimerRef.current);
      abortRef.current?.abort();
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      routesLayerRef.current = null;
      routesRendererRef.current = null;
      pinMarkers.clear();
    };
  }, []);

  useEffect(() => {
    drawRoutes();
  }, [routes, highlightRouteId]);

  // Basemap: Google Map Tiles when configured, OpenStreetMap otherwise or if
  // Google fails (bad key, quota, network). A failure is reported, never a
  // blank map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !basemap) return;
    let cancelled = false;
    let layer: L.TileLayer | null = null;
    let attribution = "";
    let onMove: (() => void) | null = null;

    const useOsm = () => {
      attribution =
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
      layer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        className: "basemap",
        attribution,
      }).addTo(map);
    };

    if (basemap.mode === "google" && basemap.key) {
      const key = basemap.key;
      createTileSession(key)
        .then((session) => {
          if (cancelled) return;
          layer = L.tileLayer(tileUrl(key, session), { maxZoom: 20, tileSize: 256 }).addTo(map);
          const refresh = async () => {
            const b = map.getBounds();
            const text = await viewportAttribution(key, session, {
              zoom: map.getZoom(),
              north: b.getNorth(),
              south: b.getSouth(),
              east: b.getEast(),
              west: b.getWest(),
            });
            if (cancelled) return;
            if (attribution) map.attributionControl.removeAttribution(attribution);
            attribution = `<span class="google-attribution">Google</span> ${esc(text)}`;
            map.attributionControl.addAttribution(attribution);
          };
          onMove = () => void refresh();
          map.on("moveend", onMove);
          void refresh();
        })
        .catch(() => {
          if (cancelled) return;
          props.current.onBasemapFallback?.();
          useOsm();
        });
    } else {
      useOsm();
    }

    return () => {
      cancelled = true;
      if (onMove) map.off("moveend", onMove);
      if (layer) layer.remove();
      if (attribution) map.attributionControl.removeAttribution(attribution);
    };
  }, [basemap]);

  // Commodity change or a live update: reload everything at the current view.
  useEffect(() => {
    void refreshAll();
  }, [sku, refreshKey]);

  // Selection only restyles; no refetch needed.
  useEffect(() => {
    if (mapRef.current && tierFor(mapRef.current.getZoom()) === "facility") draw();
  }, [selectedFacilityId]);

  useEffect(() => {
    if (flyTarget && mapRef.current) {
      goTo(mapRef.current, flyTarget.lat, flyTarget.lng, flyTarget.zoom);
    }
  }, [flyTarget]);

  // A live report lands: ring the facility so the eye goes straight to it.
  useEffect(() => {
    if (!pulse) return;
    const m = pinMarkersRef.current.get(pulse.id);
    if (!m) return;
    const base = m.getRadius();
    m.setStyle({ color: "#0ea5e9", weight: 4 }).setRadius(base + 7);
    const t = window.setTimeout(() => {
      m.setStyle({ color: "#ffffff", weight: 1.5 }).setRadius(base);
    }, 2200);
    return () => window.clearTimeout(t);
  }, [pulse]);

  return <div ref={hostRef} className="h-full w-full" />;
}

function rank(s: Pin["status"]): number {
  return s === "critical" ? 2 : s === "at_risk" ? 1 : 0;
}

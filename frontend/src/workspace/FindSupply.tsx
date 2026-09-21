/**
 * Where the medicine actually is.
 *
 * The three nearest centres that can spare this medicine *and still hold their
 * own fourteen days of cover*. That second clause is the whole point: this is
 * the optimiser's own donor split read from the short centre's side, so nothing
 * is offered here that an officer would then have to refuse. A pharmacist is
 * never shown a centre they cannot actually ask.
 *
 * The distance is a straight line multiplied by 1.3, because MAPS_MODE is osm
 * and no Routes credential is configured. It says so on every row, and the map
 * draws a dashed line rather than a solid one, because a solid line between
 * two points on a map reads as a road whatever the caption says.
 */

import { useEffect, useRef, useState } from "react";
import L from "leaflet";

import { type Donor, type FacilityDetail, type Supply, type WorkspaceSku } from "../api";
import RequestStock from "./RequestStock";
import { both } from "./labels";

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/**
 * A small map showing the donors against this centre. Direct Leaflet, as the
 * rest of this project uses — there is no react-leaflet here.
 */
function SupplyMap({ facility, donors }: { facility: FacilityDetail; donors: Donor[] }) {
  const host = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false,
      attributionControl: true,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      touchZoom: false,
      keyboard: false,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap",
    }).addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const layer = L.layerGroup().addTo(m);

    const here = L.latLng(facility.lat, facility.lng);
    L.circleMarker(here, {
      radius: 7,
      color: "#0b3d5c",
      weight: 2,
      fillColor: "#0b3d5c",
      fillOpacity: 1,
    })
      .bindTooltip(facility.name, { direction: "top" })
      .addTo(layer);

    const points = [here];
    for (const d of donors) {
      const at = L.latLng(d.lat, d.lng);
      points.push(at);
      // Dashed on purpose: this is a straight-line estimate, and a solid line
      // between two points on a map reads as a road.
      L.polyline([here, at], {
        color: "#0b3d5c",
        weight: 1.5,
        opacity: 0.6,
        dashArray: "5 5",
      }).addTo(layer);
      L.circleMarker(at, {
        radius: 6,
        color: "#1b9150",
        weight: 2,
        fillColor: "#ffffff",
        fillOpacity: 1,
      })
        .bindTooltip(`${d.name} — ${d.km} km`, { direction: "top" })
        .addTo(layer);
    }

    m.fitBounds(L.latLngBounds(points).pad(0.25), {
      animate: !prefersReducedMotion(),
    });
    return () => {
      layer.remove();
    };
  }, [facility, donors]);

  return (
    <div
      ref={host}
      role="img"
      aria-label={`Map showing ${donors.length} nearby centres with spare stock`}
      className="h-[180px] w-full rounded-md border border-line"
    />
  );
}

export default function FindSupply({
  facility,
  sku,
  supply,
  onClose,
  onRequested,
}: {
  facility: FacilityDetail;
  sku: WorkspaceSku;
  supply: Supply | null;
  onClose: () => void;
  onRequested: () => void;
}) {
  const [asking, setAsking] = useState<Donor | null>(null);

  return (
    <div className="fixed inset-0 z-[1200] flex flex-col bg-canvas">
      <header className="flex shrink-0 items-start gap-3 border-b border-line bg-panel px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
            {both("findSupply")}
          </p>
          <h2 className="mt-0.5 truncate text-[16px] font-semibold text-ink">{sku.sku_name}</h2>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="-mr-1 min-h-11 shrink-0 px-2 text-[13px] font-medium text-brand"
        >
          Close
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {!supply && <p className="text-[12.5px] text-ink-3">Looking for spare stock…</p>}

        {supply?.manual_only && (
          <div className="rounded-lg border border-risk/30 bg-risk/5 px-3.5 py-3">
            <h3 className="text-[13px] font-semibold text-ink">Manual requisition only</h3>
            <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">{supply.reason}</p>
          </div>
        )}

        {supply && !supply.manual_only && (
          <>
            <p className="mb-3 text-[12.5px] leading-relaxed text-ink-2">
              This centre needs about{" "}
              <span className="font-mono font-medium text-ink">
                {supply.units_needed.toLocaleString("en-IN")}
              </span>{" "}
              {supply.unit} to reach two weeks of cover.
            </p>

            {supply.donors.length === 0 ? (
              <p className="rounded-lg border border-line bg-panel px-3.5 py-3 text-[12.5px] leading-relaxed text-ink-2">
                {supply.reason}
              </p>
            ) : (
              <>
                <ul className="mb-3 flex flex-col gap-2.5">
                  {supply.donors.map((d) => (
                    <li key={d.facility_id}>
                      <article className="rounded-lg border border-line bg-panel px-3.5 py-3">
                        <h3 className="text-[14px] leading-snug font-semibold text-ink">
                          {d.name}
                        </h3>
                        <p className="mt-0.5 text-[11.5px] text-ink-3">{d.district}</p>
                        <dl className="mt-2 flex flex-col gap-1 text-[12.5px]">
                          <div className="flex justify-between gap-3">
                            <dt className="text-ink-2">Distance</dt>
                            <dd className="text-right font-medium text-ink">
                              {d.km} km
                              <span className="ml-1 font-normal text-ink-3">
                                straight-line estimate
                              </span>
                            </dd>
                          </div>
                          <div className="flex justify-between gap-3">
                            <dt className="text-ink-2">Can spare</dt>
                            <dd className="text-right font-medium text-ink">
                              {d.spare_units.toLocaleString("en-IN")} {supply.unit}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-3">
                            <dt className="text-ink-2">They would keep</dt>
                            <dd className="text-right font-medium text-ink">
                              {d.days_kept.toFixed(1)} days
                            </dd>
                          </div>
                        </dl>
                        <button
                          onClick={() => setAsking(d)}
                          className="mt-3 min-h-11 w-full rounded-md bg-brand px-3 text-[13px] font-medium text-white"
                        >
                          {both("requestStock")}
                        </button>
                      </article>
                    </li>
                  ))}
                </ul>

                <SupplyMap facility={facility} donors={supply.donors} />
                <p className="mt-2 text-[11px] leading-snug text-ink-3">
                  Distances are straight-line, multiplied by 1.3 to approximate road
                  travel. They are not a road route: no routing service is configured
                  for this deployment.
                </p>
              </>
            )}
          </>
        )}
      </div>

      {asking && supply && (
        <RequestStock
          facility={facility}
          donor={asking}
          supply={supply}
          onCancel={() => setAsking(null)}
          onDone={onRequested}
        />
      )}
    </div>
  );
}

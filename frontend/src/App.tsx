import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import DataNotice from "./DataNotice";
import NationalMap, {
  DISTRICT_ZOOM,
  FACILITY_ZOOM,
  type FlyTarget,
  type RouteLine,
  type ViewInfo,
} from "./NationalMap";
import {
  ApiError,
  type Bucket,
  type FacilityDetail,
  type LiveEvent,
  type Pin,
  type Plan,
  ROLE_LABEL,
  type Session,
  type Sku,
  type Summary,
  type Transfer,
  type User,
  can,
  STATUS_COLOR,
  STATUS_LABEL,
  STATUS_RULE,
  api,
  formatDays,
  formatLatency,
  submitReading,
  POLL_VISIBLE_MS,
  useLiveUpdates,
} from "./api";
import { LiveLoopPanel } from "./liveloop";
import { ActivityFeed, FacilityPanel, NationalPanel, StatePanel } from "./panels";
import { FederationPanel } from "./federation";
import { FieldSimulator } from "./field";
import { MovementsPanel } from "./movements";
import { AuditQueuePanel } from "./trustpanel";
import { RedistributionPanel, TransfersPrompt, type Trip, groupTrips } from "./transfers";

type Mode = "stock" | "transfers" | "movements" | "trust" | "federation" | "field";

const INDIA_VIEW = { lat: 22.8, lng: 81.5, zoom: 5 };

/**
 * The address bar mirrors what is on screen, so a refresh or a pasted link
 * opens the same place:  ?view=transfers&sku=ORS&facility=HFR-…&at=19.99,73.79,9
 */
interface UrlState {
  mode: Mode;
  sku: string | null;
  facility: string | null;
  at: { lat: number; lng: number; zoom: number } | null;
}

function readUrl(): UrlState {
  const q = new URLSearchParams(window.location.search);
  const at = q.get("at")?.split(",").map(Number);
  const validAt =
    at &&
    at.length === 3 &&
    at.every(Number.isFinite) &&
    Math.abs(at[0]) <= 90 &&
    Math.abs(at[1]) <= 180 &&
    at[2] >= 4 &&
    at[2] <= 18;
  return {
    mode:
      q.get("view") === "transfers"
        ? "transfers"
        : q.get("view") === "movements"
          ? "movements"
          : q.get("view") === "trust"
            ? "trust"
            : q.get("view") === "federation"
              ? "federation"
            : q.get("view") === "field"
              ? "field"
            : "stock",
    sku: q.get("sku"),
    facility: q.get("facility"),
    at: validAt ? { lat: at[0], lng: at[1], zoom: at[2] } : null,
  };
}

function pinFromDetail(d: FacilityDetail): Pin {
  return {
    id: d.id,
    name: d.name,
    type: d.type,
    district: d.district,
    state_silo: d.state_silo,
    lat: d.lat,
    lng: d.lng,
    status: d.status,
    min_days: null,
    critical_skus: d.skus.filter((s) => s.status === "critical").length,
    at_risk_skus: d.skus.filter((s) => s.status === "at_risk").length,
  };
}

function UserMenu({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const scope =
    user.role === "admin"
      ? "All of India"
      : user.role === "state_officer"
        ? user.state_silo
        : user.role === "block_mo"
          ? `${user.district}, ${user.state_silo}`
          : user.facility_id;
  const initials = user.name
    .split(/\s+/)
    .filter((w) => /^[A-Za-z]/.test(w))
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-canvas"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand text-[11px] font-semibold text-white">
          {initials || "?"}
        </span>
        <span className="hidden text-left leading-tight xl:block">
          <span className="block max-w-[160px] truncate text-[12.5px] font-medium text-ink">{user.name}</span>
          <span className="block text-[10.5px] text-ink-3">{ROLE_LABEL[user.role]}</span>
        </span>
      </button>
      {open && (
        <>
          <button className="fixed inset-0 z-[1190] cursor-default" aria-hidden="true" tabIndex={-1} onClick={() => setOpen(false)} />
          <div role="menu" className="absolute top-full right-0 z-[1200] mt-1 w-64 rounded-lg border border-line bg-panel p-1 shadow-lg">
            <div className="px-3 py-2.5">
              <div className="text-[13px] font-medium text-ink">{user.name}</div>
              <div className="truncate text-[11.5px] text-ink-3">{user.email}</div>
              <div className="mt-2 text-[11.5px] text-ink-2">
                {ROLE_LABEL[user.role]} · <span className="text-ink">{scope}</span>
              </div>
            </div>
            <div className="border-t border-line" />
            <button
              role="menuitem"
              onClick={onSignOut}
              className="w-full rounded-md px-3 py-2 text-left text-[13px] text-ink hover:bg-canvas"
            >
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function App({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const user = session.user;
  const [initialUrl] = useState(readUrl);
  const [skus, setSkus] = useState<Sku[]>([]);
  const [sku, setSku] = useState<string | null>(initialUrl.sku);
  const [view, setView] = useState<ViewInfo>({
    lat: initialUrl.at?.lat ?? INDIA_VIEW.lat,
    lng: initialUrl.at?.lng ?? INDIA_VIEW.lng,
    zoom: initialUrl.at?.zoom ?? INDIA_VIEW.zoom,
    tier: "state",
    focusState: null,
    pinsInView: 0,
    settled: true,
  });
  const [selected, setSelected] = useState<Pin | null>(null);
  const [flyTarget, setFlyTarget] = useState<FlyTarget | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [pulse, setPulse] = useState<{ id: string; nonce: number } | null>(null);
  const [toast, setToast] = useState<LiveEvent | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [states, setStates] = useState<Bucket[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>(initialUrl.mode);
  const [basemap, setBasemap] = useState<{ mode: "osm" | "google"; key: string } | null>(null);
  // Assume the mock extractor until the server says otherwise: offering the
  // live photo path against a mock backend would fail on submit.
  const [llmMode, setLlmMode] = useState<"live" | "mock">("mock");
  // Set when a trust score's evidence link is followed: the movement tab then
  // shows exactly the rows that score was computed from — same filter, same
  // query, so the two can never disagree.
  const [evidenceFor, setEvidenceFor] = useState<{ id: string; name: string } | null>(null);
  // The judge-driven loop takes the panel over while it runs; the map stays
  // visible beside it, which is the half they are meant to be watching.
  const [loopFor, setLoopFor] = useState<FacilityDetail | null>(null);
  // Which states train the shared model, reported by the federation panel so
  // the map can ring them while that tab is open.
  const [siloStates, setSiloStates] = useState<string[]>([]);
  // Holds why Google's basemap was refused or dropped, so the banner can say
  // it rather than leaving the map quietly different from what was configured.
  const [basemapFallback, setBasemapFallback] = useState<string | null>(null);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  // A plan's shortfall and manual-review lists only come back from the solve,
  // so keep the latest one per state for as long as the page is open.
  const [plans, setPlans] = useState<Record<string, Plan>>({});
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planMs, setPlanMs] = useState<number | null>(null);
  const [highlightTrip, setHighlightTrip] = useState<string | null>(null);

  const { connected, events, resyncs, pollNow } = useLiveUpdates();

  // The server said this browser missed too much to replay; reload everything.
  useEffect(() => {
    if (resyncs > 0) setRefreshKey((k) => k + 1);
  }, [resyncs]);

  const fly = useCallback((lat: number, lng: number, zoom: number) => {
    setFlyTarget({ lat, lng, zoom, nonce: Date.now() });
  }, []);

  useEffect(() => {
    api.skus().then(setSkus).catch((e) => setLoadError(String(e)));
    api
      .clientConfig()
      .then((c) => {
        setBasemap({ mode: c.maps_mode, key: c.maps_browser_key });
        setLlmMode(c.llm_mode);
      })
      .catch(() => setBasemap({ mode: "osm", key: "" }));
  }, []);

  // Open where this person works, unless a link already says where to go:
  // a state officer lands on their state, a district officer on their
  // district, facility staff on their own facility.
  const landed = useRef(false);
  useEffect(() => {
    if (landed.current || !states.length) return;
    landed.current = true;

    const openFacility = (id: string, flyThere: boolean) =>
      api
        .facility(id)
        .then((d) => {
          const pin = pinFromDetail(d);
          setSelected(pin);
          if (flyThere) fly(pin.lat, pin.lng, FACILITY_ZOOM + 3);
        })
        .catch(() => undefined);

    if (initialUrl.facility) {
      void openFacility(initialUrl.facility, !initialUrl.at);
      return;
    }
    if (initialUrl.at) return;

    if (user.role === "facility_user" && user.facility_id) {
      void openFacility(user.facility_id, true);
    } else if (user.role === "block_mo" && user.state_silo && user.district) {
      api
        .districts(null, user.state_silo)
        .then((ds) => {
          const d = ds.find((x) => x.label === user.district);
          if (d) fly(d.lat, d.lng, FACILITY_ZOOM + 1);
        })
        .catch(() => undefined);
    } else if (user.role === "state_officer" && user.state_silo) {
      const b = states.find((s) => s.key === user.state_silo);
      if (b) fly(b.lat, b.lng, Math.max(b.zoom, DISTRICT_ZOOM + 0.5));
    }
  }, [states, user, initialUrl, fly]);

  useEffect(() => {
    Promise.all([api.summary(sku), api.states(sku)])
      .then(([s, st]) => {
        setSummary(s);
        setStates(st);
        setLoadError(null);
      })
      .catch((e) => setLoadError(String(e)));
  }, [sku, refreshKey]);

  // A live report: refresh every view once (debounced), ring the facility on
  // the map, and surface where it came from.
  // Events arrive in bursts (a reading, then the status change it caused) and
  // React batches them, so look at every event not yet handled rather than
  // only the newest one.
  const lastSeq = useRef(0);
  useEffect(() => {
    const fresh = events.filter((e) => e.seq > lastSeq.current);
    if (!fresh.length) return;
    lastSeq.current = fresh[0].seq;
    // An approved transfer produces its own readings; describe it as a
    // transfer, not as two unrelated field reports.
    const decided = fresh.find((e) => e.kind === "transfer.decided");
    const received = fresh.find((e) => e.kind === "movement.received");
    const reading = fresh.find((e) => e.kind === "reading.committed" && e.source !== "transfer");
    const shown = decided ?? received ?? reading;
    if (shown) {
      setPulse({ id: shown.facility_id, nonce: shown.seq });
      setToast(shown);
    }
    const t = window.setTimeout(() => setRefreshKey((k) => k + 1), 250);
    return () => window.clearTimeout(t);
  }, [events]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  // The map redraws its own tiers mid-animation; the panel, breadcrumb and
  // selection only react once it has come to rest, so a flight between two
  // places never flashes the national view on the way.
  const onView = useCallback((v: ViewInfo) => {
    if (!v.settled) return;
    setView(v);
    if (v.tier === "state") setSelected(null);
  }, []);

  const stateName = useCallback(
    (code: string | null) => states.find((s) => s.key === code)?.label ?? code ?? "",
    [states],
  );

  const activeState = selected?.state_silo ?? (view.tier !== "state" ? view.focusState : null);

  useEffect(() => {
    if (mode !== "transfers" || !activeState) {
      setTransfers([]);
      return;
    }
    let alive = true;
    api
      .transfers(activeState)
      .then((t) => alive && setTransfers(t))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [mode, activeState, refreshKey]);

  useEffect(() => {
    setPlanError(null);
    setHighlightTrip(null);
  }, [activeState]);

  const runPlan = async () => {
    if (!activeState) return;
    setPlanning(true);
    setPlanError(null);
    const started = performance.now();
    try {
      const plan = await api.plan(activeState, sku);
      setPlanMs(performance.now() - started);
      setPlans((p) => ({ ...p, [activeState]: plan }));
      setTransfers(await api.transfers(activeState));
      pollNow();
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlanning(false);
    }
  };

  // Decisions go one transfer at a time, so a single stale item (its donor's
  // stock changed after planning) is refused without blocking the rest.
  const decideTrip = async (trip: Trip, decision: "approve" | "reject") => {
    const errs: Record<number, string> = {};
    for (const item of trip.items.filter((i) => i.status === "proposed")) {
      try {
        await api.decide(item.id, decision);
      } catch (e) {
        errs[item.id] = e instanceof ApiError ? e.message : String(e);
      }
    }
    if (activeState) setTransfers(await api.transfers(activeState));
    // Don't make the officer who just approved wait for the next poll.
    pollNow();
    return errs;
  };

  const focusTrip = (trip: Trip) => {
    setHighlightTrip(trip.id);
    const zoom = trip.km < 15 ? 11 : trip.km < 50 ? 10 : 9;
    fly((trip.from.lat + trip.to.lat) / 2, (trip.from.lng + trip.to.lng) / 2, zoom);
  };

  const routes: RouteLine[] = useMemo(() => {
    if (mode !== "transfers") return [];
    const scoped = sku ? transfers.filter((t) => t.sku_code === sku) : transfers;
    return groupTrips(scoped.filter((t) => t.status !== "rejected")).map((trip) => ({
      id: trip.id,
      from: [trip.from.lat, trip.from.lng],
      to: [trip.to.lat, trip.to.lng],
      status: trip.status === "rejected" ? "proposed" : trip.status,
      urgent: trip.urgent,
      label: `${trip.from.name} → ${trip.to.name} · ${trip.items.length} medicine${trip.items.length > 1 ? "s" : ""}`,
    }));
  }, [mode, transfers, sku]);

  const pickFacility = useCallback(
    (p: Pin) => {
      setSelected(p);
      // Choosing a facility is a request to look at that facility, so it
      // returns to the tab whose subject is one — otherwise the selection
      // would be made and nothing would change on screen. Field reports is
      // exempt: that tab reads the selection deliberately, so the simulator
      // stays pointed at whichever centre the map is on.
      setMode((m) => (m === "field" ? m : "stock"));
      fly(p.lat, p.lng, Math.max(view.zoom, FACILITY_ZOOM + 2));
    },
    [fly, view.zoom],
  );

  const goNational = () => {
    setSelected(null);
    fly(INDIA_VIEW.lat, INDIA_VIEW.lng, INDIA_VIEW.zoom);
  };

  const goState = (code: string) => {
    setSelected(null);
    const b = states.find((s) => s.key === code);
    if (b) fly(b.lat, b.lng, Math.max(b.zoom, DISTRICT_ZOOM + 0.5));
  };

  // Demo-only: sends an SMS-shaped report through the real pipeline. Offered
  // only when the server is in demo mode, and only for a facility this
  // person is allowed to report for.
  const canSendTestReports =
    session.demo_mode && (user.role !== "facility_user" || !!user.facility_id);

  const sendTestReport = async () => {
    let target: Pin | undefined;
    if (user.role === "facility_user" && user.facility_id) {
      target = pinFromDetail(await api.facility(user.facility_id));
    } else if (selected && can.report(user, selected)) {
      target = selected;
    } else {
      const stateCode =
        user.role === "admin" ? (activeState ?? states[0]?.key) : user.state_silo;
      if (!stateCode) return;
      const pins = (await api.pinsInState(stateCode, "ORS", 400)).filter((p) =>
        can.report(user, p),
      );
      // A facility that is currently fine, so the report visibly changes it.
      target = [...pins].reverse().find((p) => p.status === "healthy") ?? pins[0];
    }
    if (!target) return;
    setSelected(target);
    fly(target.lat, target.lng, Math.max(view.zoom, FACILITY_ZOOM + 2));
    try {
      await submitReading({
        facility_id: target.id,
        sku_code: "ORS",
        qty_on_hand: 4,
        source: "sms",
        reporter_ref: `test_${Math.random().toString(16).slice(2, 8)}`,
        confidence: 0.97,
      });
      pollNow();
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  };

  // Keep the address bar in step with the settled view.
  useEffect(() => {
    const q = new URLSearchParams();
    if (mode !== "stock") q.set("view", mode);
    if (sku) q.set("sku", sku);
    if (selected) q.set("facility", selected.id);
    q.set("at", `${view.lat.toFixed(4)},${view.lng.toFixed(4)},${Number(view.zoom.toFixed(1))}`);
    const next = `${window.location.pathname}?${q.toString().replace(/%2C/g, ",")}`;
    if (next !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, "", next);
    }
  }, [mode, sku, selected, view.lat, view.lng, view.zoom]);

  const medicineName = useMemo(
    () => (sku ? (skus.find((s) => s.code === sku)?.name ?? sku) : "All medicines"),
    [sku, skus],
  );

  const tierHint =
    mode === "transfers" && activeState
      ? routes.length
        ? `${routes.length.toLocaleString("en-IN")} trips in ${stateName(activeState)}. Dashed lines are proposed, solid green are approved.`
        : `Generate a plan to see proposed trips across ${stateName(activeState)}.`
      : view.tier === "state"
        ? mode === "transfers"
          ? "Pick a state to plan its transfers."
          : "Showing every state. Scroll to zoom, or click a state."
        : view.tier === "district"
          ? `Showing districts${activeState ? ` around ${stateName(activeState)}` : ""}. Zoom in further for individual facilities.`
          : `${view.pinsInView.toLocaleString("en-IN")} health centres in view. Click one for its medicine stock.`;

  return (
    <div className="flex h-full flex-col bg-canvas font-sans text-ink">
      {/* ------------------------------------------------------ top bar --- */}
      <header className="z-[1100] flex h-14 shrink-0 items-center gap-5 border-b border-line bg-panel px-4">
        <div className="flex items-center gap-2.5">
          <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
            <rect width="26" height="26" rx="6" fill="#0b3d5c" />
            <path d="M13 6v14M6 13h14" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
            <circle cx="19.5" cy="6.5" r="3" fill="#e0900e" stroke="#0b3d5c" strokeWidth="1.5" />
          </svg>
          <div className="leading-tight">
            <div className="text-[14.5px] font-semibold tracking-tight text-ink">SwasthSetu</div>
            <div className="text-[10.5px] text-ink-3">National Health Supply Command</div>
          </div>
        </div>

        <nav aria-label="Location" className="flex min-w-0 items-center gap-1.5 text-[13px]">
          <span className="text-line">|</span>
          <button
            onClick={goNational}
            className={`rounded px-1.5 py-0.5 hover:bg-canvas ${activeState ? "text-brand" : "font-medium text-ink"}`}
          >
            India
          </button>
          {activeState && (
            <>
              <span className="text-ink-3">›</span>
              <button
                onClick={() => goState(activeState)}
                className={`truncate rounded px-1.5 py-0.5 hover:bg-canvas ${selected ? "text-brand" : "font-medium text-ink"}`}
              >
                {stateName(activeState)}
              </button>
            </>
          )}
          {selected && (
            <>
              <span className="text-ink-3">›</span>
              <span className="truncate px-1.5 font-medium text-ink">{selected.name}</span>
            </>
          )}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div role="tablist" aria-label="View" className="flex rounded-md border border-line bg-canvas p-0.5">
            {(
              [
                ["stock", "Stock levels"],
                ["transfers", "Redistribution"],
                ["movements", "Movements"],
                ["trust", "Data trust"],
                ["federation", "Federation"],
                ["field", "Field reports"],
              ] as const
            ).map(([m, label]) => (
              <button
                key={m}
                role="tab"
                aria-selected={mode === m}
                onClick={() => setMode(m)}
                className={`h-7 rounded px-3 text-[12.5px] font-medium ${
                  mode === m ? "bg-panel text-ink shadow-sm" : "text-ink-3 hover:text-ink-2"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <label className="flex items-center gap-2 text-[12px] text-ink-2">
            Medicine
            <select
              value={sku ?? ""}
              onChange={(e) => setSku(e.target.value || null)}
              className="h-8 min-w-[210px] rounded-md border border-line bg-panel px-2 text-[12.5px] font-medium text-ink focus:border-brand focus:outline-none"
            >
              <option value="">All medicines (lowest stocked)</option>
              {skus.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>

          <span
            className="rounded-md border border-line px-2 py-1 text-[11px] text-ink-2"
            title="District locations are real. Facility positions and stock levels are simulated, covering roughly 12% of the national PHC network."
          >
            Simulated data
          </span>

          <span
            className="flex items-center gap-1.5 text-[12px] font-medium"
            style={{ color: connected ? STATUS_COLOR.healthy : "#7d858f" }}
            title={
              connected
                ? `Checking for updates every ${POLL_VISIBLE_MS / 1000} seconds`
                : "Cannot reach the server; retrying"
            }
          >
            <span className={`h-2 w-2 rounded-full ${connected ? "live-dot" : ""}`}
              style={{ background: connected ? STATUS_COLOR.healthy : "#b3b9c0" }} />
            {connected ? "Live" : "Reconnecting"}
          </span>

          <span className="h-6 w-px bg-line" aria-hidden="true" />
          <UserMenu user={user} onSignOut={onSignOut} />
        </div>
      </header>

      <main className="flex min-h-0 flex-1">
        {/* ---------------------------------------------------- panel --- */}
        <aside className="z-[1000] flex w-[400px] shrink-0 flex-col border-r border-line bg-panel">
          {loopFor ? (
            <LiveLoopPanel
              facility={loopFor}
              user={user}
              events={events}
              onClose={() => setLoopFor(null)}
              onChanged={() => {
                setRefreshKey((k) => k + 1);
                pollNow();
              }}
              onFocus={(lat, lng) => fly(lat, lng, Math.max(view.zoom, FACILITY_ZOOM + 2))}
            />
          ) : mode === "field" ? (
            // Ahead of the facility panel on purpose: choosing the tab is a
            // decision to look at the channels, and it still reads whichever
            // centre is selected on the map so the two stay in step.
            <FieldSimulator facilityId={selected ? selected.id : null} />
          ) : selected && mode === "stock" ? (
            // Gated on the tab, not just on `selected`. Without the mode
            // check this branch shadows every panel below it, so once a
            // facility was picked on the map, Redistribution, Data trust,
            // Movements and Federation all kept rendering this same drawer
            // and looked like tabs that would not load.
            <FacilityPanel
              id={selected.id}
              sku={sku}
              skus={skus}
              refreshKey={refreshKey}
              stateName={stateName(selected.state_silo)}
              user={user}
              demoMode={session.demo_mode}
              llmMode={llmMode}
              onSimulateStockOut={setLoopFor}
              onOpenEvidence={(e, facility) => {
                if (e.tab !== "movements") return;
                setEvidenceFor(facility);
                setMode("movements");
                setSelected(null);
              }}
              onBack={() => setSelected(null)}
            />
          ) : mode === "federation" ? (
            <FederationPanel refreshKey={refreshKey} onSilos={setSiloStates} stateName={stateName} />
          ) : mode === "trust" ? (
            <AuditQueuePanel
              // One value decides both the request and the heading. An
              // administrator follows the map; every other role is pinned to
              // its own patch by the server regardless of what is asked. The
              // key remounts on a change of scope, so the previous scope's
              // rows never sit under the new scope's heading while the
              // replacement request is still in flight.
              key={user.state_silo ?? activeState ?? "national"}
              state={user.state_silo ?? activeState}
              states={states}
              onPickState={goState}
              stateLabel={
                user.state_silo
                  ? stateName(user.state_silo)
                  : activeState
                    ? stateName(activeState)
                    : "India"
              }
              refreshKey={refreshKey}
              onPick={(row) => {
                // The queue knows where the facility is, but the drawer wants
                // its full state, so load it and select it the same way a map
                // click does.
                api
                  .facility(row.facility_id)
                  .then((d) => pickFacility(pinFromDetail(d)))
                  .catch(() => fly(row.lat, row.lng, FACILITY_ZOOM + 2));
              }}
            />
          ) : mode === "movements" ? (
            <MovementsPanel
              key={activeState ?? user.state_silo ?? "national"}
              facility={evidenceFor}
              initialView={evidenceFor ? "attention" : undefined}
              onClearFacility={() => setEvidenceFor(null)}
              // The server narrows the ledger to what this role may see, so
              // the heading has to name that scope, not wherever the map
              // happens to be pointed.
              stateLabel={
                user.role === "facility_user"
                  ? "your facility"
                  : user.role === "block_mo"
                    ? `${user.district ?? ""}, ${stateName(user.state_silo ?? "")}`
                    : user.state_silo
                      ? stateName(user.state_silo)
                      : activeState
                        ? stateName(activeState)
                        : "India"
              }
              state={activeState}
              sku={sku}
              user={user}
              refreshKey={refreshKey}
              onReceipt={() => setRefreshKey((k) => k + 1)}
            />
          ) : mode === "transfers" ? (
            activeState ? (
              <RedistributionPanel
                key={activeState}
                stateLabel={stateName(activeState)}
                sku={sku}
                skus={skus}
                transfers={transfers}
                plan={plans[activeState] ?? null}
                planning={planning}
                planError={planError}
                planMs={planMs}
                highlightTripId={highlightTrip}
                onPlan={runPlan}
                canPlan={can.planState(user, activeState)}
                canDecide={(trip) =>
                  can.decideTransfer(
                    user,
                    { state: activeState, district: trip.from.district },
                    { state: activeState, district: trip.to.district },
                  )
                }
                onDecideTrip={decideTrip}
                onHoverTrip={setHighlightTrip}
                onFocusTrip={focusTrip}
              />
            ) : (
              <TransfersPrompt
                states={states}
                onPickState={(b) => fly(b.lat, b.lng, Math.max(b.zoom, DISTRICT_ZOOM + 0.5))}
              />
            )
          ) : activeState ? (
            <StatePanel
              key={`${activeState}:${sku ?? "all"}`}
              stateCode={activeState}
              stateLabel={stateName(activeState)}
              sku={sku}
              skus={skus}
              refreshKey={refreshKey}
              selectedFacilityId={null}
              onPickDistrict={(b) => fly(b.lat, b.lng, FACILITY_ZOOM + 1)}
              onPickFacility={pickFacility}
            />
          ) : (
            <NationalPanel
              summary={summary}
              states={states}
              sku={sku}
              skus={skus}
              onPickState={(b) => fly(b.lat, b.lng, Math.max(b.zoom, DISTRICT_ZOOM + 0.5))}
            />
          )}
          <ActivityFeed
            events={events}
            onSimulate={sendTestReport}
            canSimulate={canSendTestReports && states.length > 0}
          />
        </aside>

        {/* ------------------------------------------------------ map --- */}
        <section className="relative min-w-0 flex-1">
          <NationalMap
            sku={sku}
            refreshKey={refreshKey}
            siloStates={siloStates}
            selectedFacilityId={selected?.id ?? null}
            pulse={pulse}
            flyTarget={flyTarget}
            initialView={initialUrl.at}
            basemap={basemap}
            onBasemapFallback={(reason) => setBasemapFallback(reason)}
            routes={routes}
            highlightRouteId={highlightTrip}
            onView={onView}
            onSelectFacility={pickFacility}
            onSelectRoute={setHighlightTrip}
          />

          <div className="pointer-events-none absolute top-3 left-3 z-[900] max-w-[440px]">
            <div className="rounded-lg border border-line bg-panel/95 px-3 py-2 shadow-sm backdrop-blur">
              <div className="text-[11px] font-medium text-ink-3">{medicineName}</div>
              <div className="text-[12.5px] text-ink">{tierHint}</div>
            </div>
          </div>

          {view.tier !== "state" && (
            <button
              onClick={goNational}
              className="absolute top-3 right-3 z-[900] rounded-lg border border-line bg-panel px-3 py-1.5 text-[12px] font-medium text-ink shadow-sm hover:bg-canvas"
            >
              Back to all of India
            </button>
          )}

          {toast && (
            <div
              role="status"
              className="toast-in absolute top-14 right-3 z-[950] w-[300px] rounded-lg border border-line bg-panel p-3 shadow-lg"
            >
              <div className="flex items-center justify-between">
                <span className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
                  {toast.kind === "transfer.decided"
                    ? `Transfer ${toast.decision}`
                    : toast.kind === "movement.received"
                      ? "Delivery confirmed"
                      : "New field report"}
                </span>
                <span className="font-mono text-[10.5px] text-ink-3">{formatLatency(toast.latencyMs)}</span>
              </div>
              {toast.kind === "transfer.decided" ? (
                <>
                  <div className="mt-1 text-[13px] font-medium text-ink">{toast.to_name}</div>
                  <div className="mt-0.5 text-[12px] text-ink-2">
                    {toast.decision === "approved" ? "Receiving" : "Will not receive"}{" "}
                    <span className="font-mono">{Math.round(toast.qty ?? 0)}</span> {toast.sku_code} from{" "}
                    {toast.from_name}
                  </div>
                  {toast.decision === "approved" && (
                    <div className="mt-1 text-[11px] text-ink-3">
                      Cover once delivered{" "}
                      <span style={{ color: STATUS_COLOR.healthy }}>
                        {formatDays(toast.to_days_after)}
                      </span>
                    </div>
                  )}
                </>
              ) : toast.kind === "movement.received" ? (
                <>
                  <div className="mt-1 text-[13px] font-medium text-ink">{toast.facility_name}</div>
                  <div className="mt-0.5 text-[12px] text-ink-2">
                    Confirmed <span className="font-mono">{Math.round(toast.qty_received ?? 0)}</span> of{" "}
                    <span className="font-mono">{Math.round(toast.qty_dispatched ?? 0)}</span>{" "}
                    {toast.sku_name ?? toast.sku_code}
                  </div>
                  <div className="mt-1 text-[11px] text-ink-3">
                    {toast.status === "received" ? (
                      <>Batch {toast.batch_id} settled in full</>
                    ) : (
                      <span style={{ color: STATUS_COLOR.critical }}>
                        Batch {toast.batch_id} recorded as {toast.status}
                      </span>
                    )}
                    {toast.received_via && ` · via ${toast.received_via.toUpperCase()}`}
                  </div>
                </>
              ) : (
                <>
                  <div className="mt-1 text-[13px] font-medium text-ink">{toast.facility_name}</div>
                  <div className="mt-0.5 text-[12px] text-ink-2">
                    {toast.sku_code} reported at <span className="font-mono">{toast.qty_on_hand}</span>{" "}
                    ·{" "}
                    <span style={{ color: STATUS_COLOR.critical }}>
                      {formatDays(toast.days_of_stock)} left
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-ink-3">
                    via {toast.source?.toUpperCase()} · sender {toast.reporter_ref?.slice(0, 10)}
                  </div>
                </>
              )}
            </div>
          )}

          <div className="absolute bottom-6 left-3 z-[900] rounded-lg border border-line bg-panel/95 px-3 py-2.5 shadow-sm backdrop-blur">
            {(["critical", "at_risk", "healthy"] as const).map((s) => (
              <div key={s} className="flex items-center gap-2 py-0.5 text-[11.5px]">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: STATUS_COLOR[s] }} />
                <span className="w-16 font-medium text-ink">{STATUS_LABEL[s]}</span>
                <span className="text-ink-3">{STATUS_RULE[s]}</span>
              </div>
            ))}
            {view.tier !== "facility" && (
              <div className="mt-1.5 border-t border-line pt-1.5 text-[10.5px] text-ink-3">
                Ring shows share of facilities · centre number is critical count
              </div>
            )}
          </div>

          {basemapFallback && (
            <div
              role="status"
              className="absolute bottom-6 left-1/2 z-[900] -translate-x-1/2 rounded-md border border-line bg-panel px-3 py-1.5 text-[11.5px] text-ink-2 shadow-sm"
            >
              Showing OpenStreetMap — {basemapFallback}
            </div>
          )}

          {loadError && (
            <div className="absolute inset-x-0 top-20 z-[1000] mx-auto w-fit rounded-lg border border-crit/30 bg-panel px-4 py-2 text-[12.5px] text-crit shadow">
              Could not reach the API ({loadError}). Is the backend running on port 8000?
            </div>
          )}
        </section>
      </main>

      {/* ------------------------------------------------- data notice --- */}
      <footer className="z-[1100] shrink-0 border-t border-line bg-panel px-4 py-1.5">
        <DataNotice />
      </footer>
    </div>
  );
}

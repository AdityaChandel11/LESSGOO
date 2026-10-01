import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import DataNotice from "./DataNotice";
import NationalMap, {
  DISTRICT_ZOOM,
  FACILITY_ZOOM,
  type FlyTarget,
  type OutbreakMark,
  type RouteLine,
  type ViewInfo,
} from "./NationalMap";
import {
  ApiError,
  type Bucket,
  type FacilityDetail,
  type LiveEvent,
  type NextPair,
  type Pin,
  type Plan,
  ROLE_LABEL,
  type Session,
  type Sku,
  type Summary,
  type Transfer,
  type User,
  can,
  heldNote,
  HELD_HINDI,
  inDemoSandbox,
  readsRows,
  rowsScope,
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
import { LiveLoopPanel, SANDBOX, inSandbox, pickSku } from "./liveloop";
import { ActivityFeed, FacilityPanel, NationalPanel, StatePanel } from "./panels";
import { FederationPanel } from "./federation";
import { FieldSimulator } from "./field";
import { MovementsPanel } from "./movements";
import { AuditQueuePanel } from "./trustpanel";
import { OutbreakWarnings } from "./outbreaks";
import { NextWarnings } from "./nextwarnings";
import { RedistributionPanel, TransfersPrompt, type Trip, groupTrips } from "./transfers";
import { SEED_RULES } from "./seedRules";

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

function sinceWords(at: number, now: number): string {
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
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
        <span className="hidden text-left leading-tight 2xl:block">
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
  // Set when the loop was opened by "Simulate emergency", which starts it too.
  const [loopAuto, setLoopAuto] = useState(false);
  // "Simulate emergency" runs the outbreak chain (fix #44); a facility's own
  // button runs the stock-out drill.
  const [loopKind, setLoopKind] = useState<"stockout" | "outbreak">("stockout");
  const [emergencyBusy, setEmergencyBusy] = useState(false);
  const [feedOpen, setFeedOpen] = useState(false);
  const [emergencyError, setEmergencyError] = useState<string | null>(null);
  // Which states train the shared model, reported by the federation panel so
  // the map can ring them while that tab is open.
  const [siloStates, setSiloStates] = useState<string[]>([]);
  // Fix #59: districts with an active outbreak, marked on the map in every tab.
  const [outbreakMarks, setOutbreakMarks] = useState<OutbreakMark[]>([]);
  useEffect(() => {
    let alive = true;
    api
      .activeOutbreaks(null)
      .then((a) => {
        if (!alive) return;
        setOutbreakMarks(
          a.outbreaks.flatMap((o) =>
            o.lat != null && o.lng != null
              ? [{ id: o.id, lat: o.lat, lng: o.lng, label: `${o.disease} · ${o.district}` }]
              : [],
          ),
        );
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [refreshKey]);
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
  // When the newest change this browser has seen happened, and a clock that
  // ticks every 30 s so "2 min ago" stays true without a re-poll.
  const lastChange = events.length ? Math.max(...events.map((e) => e.receivedAt - e.latencyMs)) : null;
  const [clockNow, setClockNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setClockNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

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
    // An event about a centre this reader may not read (fix #77) still
    // refreshes the totals below, but has nothing to show or to pulse.
    const named = fresh.filter((e) => !e.withheld);
    const decided = named.find((e) => e.kind === "transfer.decided");
    const received = named.find((e) => e.kind === "movement.received");
    const reading = named.find((e) => e.kind === "reading.committed" && e.source !== "transfer");
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
    // Nothing moves on a declined or withdrawn request, so neither is a route.
    const moving = scoped.filter((t) => t.status !== "rejected" && t.status !== "cancelled");
    return groupTrips(moving).map((trip) => ({
      id: trip.id,
      from: [trip.from.lat, trip.from.lng],
      to: [trip.to.lat, trip.to.lng],
      status:
        trip.status === "rejected" || trip.status === "cancelled" ? "proposed" : trip.status,
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

  // Fix #45: a warning opens its own recommendations — the Redistribution
  // tab, on that state and that medicine.
  const openRecommendations = (p: NextPair) => {
    setSku(p.sku_code);
    goState(p.state);
    setMode("transfers");
  };

  // Demo-only: the whole emergency chain in one click, confined to the
  // sandbox district and offered only to someone who may write there.
  const canRunEmergency =
    session.demo_mode &&
    can.report(user, { id: "", state_silo: SANDBOX.state, district: SANDBOX.district }) &&
    can.planState(user, SANDBOX.state);

  const startEmergency = async () => {
    setEmergencyBusy(true);
    setEmergencyError(null);
    try {
      // The healthiest facilities first: the drop is largest and most honest
      // where there was the most cover to lose.
      const pins = (await api.pinsInState(SANDBOX.state, null, 400))
        .filter((p) => p.district === SANDBOX.district)
        .sort((a, b) => (b.min_days ?? 0) - (a.min_days ?? 0));
      for (const pin of pins.slice(0, 5)) {
        const detail = await api.facility(pin.id);
        if (pickSku(detail.skus)) {
          setSelected(null);
          setLoopKind("outbreak");
          setLoopAuto(true);
          setLoopFor(detail);
          return;
        }
      }
      setEmergencyError(`No facility in ${SANDBOX.label} has a medicine with cover left to lose.`);
    } catch (e) {
      setEmergencyError(e instanceof ApiError ? e.message : "Could not start the drill");
    } finally {
      setEmergencyBusy(false);
    }
  };

  // Demo-only: sends an SMS-shaped report through the real pipeline, always
  // for a centre inside the sandbox district. It used to pick a healthy centre
  // anywhere the account could reach — for the administrator, anywhere in
  // India — and paint it red for real (fix list #79).
  const canSendTestReports =
    session.demo_mode &&
    can.report(user, { id: "", state_silo: SANDBOX.state, district: SANDBOX.district });

  const sendTestReport = async () => {
    let target: Pin | undefined;
    if (selected && inSandbox(selected) && can.report(user, selected)) {
      target = selected;
    } else {
      const pins = (await api.pinsInState(SANDBOX.state, "ORS", 400)).filter(
        (p) => inSandbox(p) && can.report(user, p),
      );
      // A facility that is currently fine, so the report visibly changes it.
      target = [...pins].reverse().find((p) => p.status === "healthy") ?? pins[0];
    }
    if (!target) {
      setLoadError(`No centre in ${SANDBOX.label} can take a test report right now.`);
      return;
    }
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

  // Keep the address bar in step with the settled view. Moving to another
  // place — tab, state, facility or medicine — adds a history entry, so the
  // browser's Back returns to it (fix #47); panning only replaces the entry.
  const lastPlace = useRef<string | null>(null);
  const restoringUntil = useRef(0);
  useEffect(() => {
    const q = new URLSearchParams();
    if (mode !== "stock") q.set("view", mode);
    if (sku) q.set("sku", sku);
    if (selected) q.set("facility", selected.id);
    q.set("at", `${view.lat.toFixed(4)},${view.lng.toFixed(4)},${Number(view.zoom.toFixed(1))}`);
    const next = `${window.location.pathname}?${q.toString().replace(/%2C/g, ",")}`;
    const place = [mode, activeState ?? "", selected?.id ?? "", sku ?? ""].join("|");
    const moved = lastPlace.current !== null && place !== lastPlace.current;
    lastPlace.current = place;
    if (next === `${window.location.pathname}${window.location.search}`) return;
    if (moved && Date.now() > restoringUntil.current) {
      window.history.pushState(null, "", next);
    } else {
      window.history.replaceState(null, "", next);
    }
  }, [mode, sku, selected, activeState, view.lat, view.lng, view.zoom]);

  // Back and Forward: put the screen where that entry says. While the map
  // flies there, its intermediate views replace the entry rather than add.
  useEffect(() => {
    const onPop = () => {
      const u = readUrl();
      restoringUntil.current = Date.now() + 2500;
      setMode(u.mode);
      setSku(u.sku);
      if (u.facility) {
        api
          .facility(u.facility)
          .then((d) => setSelected(pinFromDetail(d)))
          .catch(() => setSelected(null));
      } else {
        setSelected(null);
      }
      if (u.at) fly(u.at.lat, u.at.lng, u.at.zoom);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [fly]);

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
          : activeState && !readsRows(user, activeState)
            ? `${heldNote(user, stateName(activeState))} Districts are shown instead of centres.`
            : `${view.pinsInView.toLocaleString("en-IN")} health centres in view. Click one for its medicine stock.`;

  return (
    <div className="flex h-full flex-col bg-canvas font-sans text-ink">
      <a
        href="#console-panel"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[2000] focus:rounded focus:bg-panel focus:px-3 focus:py-2 focus:text-[13px] focus:text-ink"
      >
        Skip to main content
      </a>
      {/* ------------------------------------------------------ top bar --- */}
      <header className="z-[1100] flex shrink-0 flex-wrap items-center gap-x-3 border-b-2 border-brand bg-panel md:h-14 md:flex-nowrap md:pr-4 min-[1360px]:gap-5">
        {/* The mark sits on a solid brand block — flat, no gradient — so the
            product reads as one identity before any data does. */}
        <div className="flex h-12 shrink-0 items-center gap-2.5 bg-brand pr-4 pl-4 whitespace-nowrap text-white md:h-full">
          <svg width="28" height="28" viewBox="0 0 26 26" aria-hidden="true">
            <rect width="26" height="26" rx="6" fill="#fff" />
            <path d="M13 6v14M6 13h14" stroke="#0b3d5c" strokeWidth="3" strokeLinecap="round" />
            <circle cx="19.5" cy="6.5" r="3" fill="#e0900e" stroke="#fff" strokeWidth="1.5" />
          </svg>
          <div className="leading-tight">
            <div className="text-[15.5px] font-semibold tracking-tight">
              SwasthSetu{" "}
              <span className="ml-0.5 text-[13px] font-medium text-white/85">स्वस्थसेतु</span>
            </div>
            <div className="hidden text-[10.5px] text-white/80 min-[1500px]:block">National Health Supply Command</div>
          </div>
        </div>

        <nav aria-label="Location" className="hidden min-w-0 shrink-0 items-center gap-1.5 text-[13px] whitespace-nowrap md:flex">
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
                // The current level is never cut short (fix #47); a long
                // facility name after it truncates instead.
                className={`rounded px-1.5 py-0.5 whitespace-nowrap hover:bg-canvas ${selected ? "text-brand" : "font-medium text-ink"}`}
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

        <div className="order-last flex w-full flex-wrap items-center gap-2 px-3 py-2 md:order-none md:ml-auto md:w-auto md:flex-nowrap md:p-0 min-[1500px]:gap-3">
          <label className="md:hidden">
            <span className="sr-only">View</span>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as Mode)}
              className="h-8 rounded-md border border-line bg-panel px-2 text-[12.5px] font-medium text-ink"
            >
              <option value="stock">Stock levels</option>
              <option value="transfers">Redistribution</option>
              <option value="movements">Movements</option>
              <option value="trust">Data trust</option>
              <option value="federation">Federation</option>
              <option value="field">Field reports</option>
            </select>
          </label>
          <div role="tablist" aria-label="View" className="hidden rounded-md border border-line bg-canvas p-0.5 md:flex">
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
                className={`h-7 rounded px-2 text-[12px] font-medium whitespace-nowrap min-[1360px]:px-2.5 min-[1360px]:text-[12.5px] ${
                  mode === m ? "bg-panel text-ink shadow-sm" : "text-ink-3 hover:text-ink-2"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <label className="flex items-center gap-2 text-[12px] text-ink-2">
            <span className="sr-only 2xl:not-sr-only">Medicine</span>
            <select
              value={sku ?? ""}
              onChange={(e) => setSku(e.target.value || null)}
              className="h-8 w-[150px] rounded-md min-[1500px]:w-[190px] 2xl:w-[210px] border border-line bg-panel px-2 text-[12.5px] font-medium text-ink focus:border-brand focus:outline-none"
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
            className="hidden rounded-md border border-line px-2 py-1 text-[11px] whitespace-nowrap text-ink-2 md:inline"
            title={`District locations are real. Facility positions and stock levels are simulated: ${SEED_RULES.seededCentres.toLocaleString("en-IN")} synthetic centres, a sample for the demo, not India's real network.`}
          >
            Simulated data
          </span>

          {/* Field reports moved off the bottom of the panel into a badge
              and drawer, so the panel's list keeps its height (fix #52). */}
          <div className="relative">
            <button
              onClick={() => setFeedOpen((o) => !o)}
              aria-expanded={feedOpen}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[12px] font-medium whitespace-nowrap text-ink-2 hover:bg-canvas"
              title="Field reports this session"
            >
              Reports
              <span className="font-mono text-[11px] text-ink-3">
                {events.filter((e) => e.kind === "reading.committed").length}
              </span>
            </button>
            {feedOpen && (
              <div className="absolute right-0 top-full z-[1100] mt-1 w-[360px] max-w-[90vw] overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
                <ActivityFeed
                  events={events}
                  onSimulate={sendTestReport}
                  canSimulate={canSendTestReports}
                  sandboxDistrict={SANDBOX.district}
                />
              </div>
            )}
          </div>

          <span
            className="flex items-center gap-1.5 text-[12px] font-medium"
            style={{ color: connected ? STATUS_COLOR.healthy : "#7d858f" }}
            title={
              connected
                ? `Checking for updates every ${POLL_VISIBLE_MS / 1000} seconds · ${lastChange === null ? "no change since you opened this page" : `last change ${sinceWords(lastChange, clockNow)}`}`
                : "Cannot reach the server; retrying"
            }
          >
            <span className={`h-2 w-2 rounded-full ${connected ? "live-dot" : ""}`}
              style={{ background: connected ? STATUS_COLOR.healthy : "#b3b9c0" }} />
            {connected ? "Live" : "Reconnecting"}
            {/* Fix #62: "Live" says how recent, not just that polling works. */}
            {connected && (
              <span className="hidden font-normal text-ink-3 min-[1500px]:inline">
                {" · "}
                {lastChange === null ? "no change since you opened this" : `last change ${sinceWords(lastChange, clockNow)}`}
              </span>
            )}
          </span>

          <span className="h-6 w-px bg-line" aria-hidden="true" />
          <UserMenu user={user} onSignOut={onSignOut} />
        </div>
      </header>

      {user.demo_sandbox && (
        // Anyone can enter a demo account, so it looks everywhere but changes
        // only one district (auth.demo_may_write). Said once, up front, rather
        // than discovered as a refusal.
        <p className="shrink-0 border-b border-line bg-canvas px-4 py-1 text-[11.5px] text-ink-2">
          Public demo: you can view every state. Changes are limited to the sandbox,{" "}
          <span className="font-medium text-ink">{user.demo_sandbox.label}</span>.{" "}
          <span lang="hi">सार्वजनिक डेमो: बदलाव केवल {user.demo_sandbox.district} सैंडबॉक्स में किए जा सकते हैं।</span>
        </p>
      )}

      <main className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* ---------------------------------------------------- panel --- */}
        <aside id="console-panel" tabIndex={-1} className="z-[1000] order-2 flex min-h-0 w-full flex-1 flex-col border-r border-line bg-panel md:order-1 md:w-[400px] md:flex-none md:shrink-0">
          {loopFor ? (
            <LiveLoopPanel
              key={`${loopFor.id}:${loopAuto}:${loopKind}`}
              facility={loopFor}
              kind={loopKind}
              autoStart={loopAuto}
              user={user}
              events={events}
              onClose={() => {
                setLoopFor(null);
                setLoopAuto(false);
              }}
              onOpenFederation={() => {
                setLoopFor(null);
                setLoopAuto(false);
                setSelected(null);
                setMode("federation");
              }}
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
            // A handset states its centre's facts, so it is offered only to
            // whoever may state them (can.report, fix list #24 on #74's rule).
            <FieldSimulator
              facilityId={selected && can.report(user, selected) ? selected.id : null}
              blockedNote={
                !selected
                  ? undefined
                  : user.demo_sandbox && !inDemoSandbox(user, selected.state_silo, selected.district)
                    ? `Public demo: messages can be sent only as handsets of centres in ${user.demo_sandbox.label}. Choose one of those on the map.`
                    : !can.report(user, selected)
                      ? "Only a centre's own staff send as its registered handsets. Officers can chase the centre for a report from its panel."
                      : undefined
              }
            />
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
              onSimulateStockOut={(d) => {
                setLoopKind("stockout");
                setLoopFor(d);
              }}
              onOpenEvidence={(e, facility) => {
                if (e.tab !== "movements") return;
                setEvidenceFor(facility);
                setMode("movements");
                setSelected(null);
              }}
              onBack={() => setSelected(null)}
            />
          ) : mode === "federation" ? (
            <>
            {/* Fix #45 (absorbs #2): the same strip, only the dates that rest
                on the shared model's fresh forecast. */}
            <NextWarnings
              state={null}
              stateLabel="India"
              refreshKey={refreshKey}
              forecastOnly
              onOpen={openRecommendations}
            />
            <FederationPanel
              refreshKey={refreshKey}
              onSilos={setSiloStates}
              stateName={stateName}
              canTrain={user.role === "admin"}
              onOpenLedger={(code) => {
                // The rows the weighting is computed from: that state's ledger.
                goState(code);
                setMode("movements");
              }}
            />
            </>
          ) : mode === "trust" && user.role === "admin" && !readsRows(user, activeState) ? (
            // Fix #77: the queue is a list of named centres with their
            // evidence, so it belongs to that state's and district's officers.
            <div className="p-4">
              <div className="text-[11px] font-semibold tracking-[0.09em] text-ink-3 uppercase">Data trust</div>
              <h2 className="mt-1 text-[18px] font-semibold tracking-tight text-ink">
                {activeState ? stateName(activeState) : "India"}
              </h2>
              <p role="note" className="mt-3 rounded border border-line bg-canvas px-3 py-2 text-[12.5px] leading-snug text-ink-2">
                {heldNote(user, activeState ? stateName(activeState) : null)} The audit queue names
                centres and shows their evidence; sign in as that state's officer to open it.
                <span lang="hi" className="mt-1 block text-ink-3">{HELD_HINDI}</span>
              </p>
            </div>
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
                stateCode={activeState}
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
                viewOnlyNote={
                  user.demo_sandbox && user.demo_sandbox.state !== activeState
                    ? `View only in the public demo. Plans can be recomputed for ${stateName(user.demo_sandbox.state)} only, where the sandbox is.`
                    : undefined
                }
                canDecide={(trip) =>
                  can.decideTransfer(user, {
                    fromId: trip.from.id,
                    state: activeState,
                    fromDistrict: trip.from.district,
                    toDistrict: trip.to.district,
                  })
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
            <>
            <NextWarnings
              state={activeState}
              stateLabel={stateName(activeState)}
              refreshKey={refreshKey}
              onOpen={openRecommendations}
            />
            <OutbreakWarnings
              state={activeState}
              stateLabel={stateName(activeState)}
              user={user}
              refreshKey={refreshKey}
              onOpenTransfers={() => setMode("transfers")}
            />
            <StatePanel
              key={`${activeState}:${sku ?? "all"}`}
              stateCode={activeState}
              stateLabel={stateName(activeState)}
              heldRows={readsRows(user, activeState) ? null : heldNote(user, stateName(activeState))}
              sku={sku}
              skus={skus}
              refreshKey={refreshKey}
              selectedFacilityId={null}
              onPickDistrict={(b) => fly(b.lat, b.lng, FACILITY_ZOOM + 1)}
              onPickFacility={pickFacility}
            />
            </>
          ) : (
            <>
            <NextWarnings
              state={null}
              stateLabel="India"
              refreshKey={refreshKey}
              onOpen={openRecommendations}
            />
            <OutbreakWarnings
              state={null}
              stateLabel="India"
              user={user}
              refreshKey={refreshKey}
              onOpenTransfers={() => setMode("transfers")}
            />
            <NationalPanel
              summary={summary}
              states={states}
              sku={sku}
              skus={skus}
              onPickState={(b) => fly(b.lat, b.lng, Math.max(b.zoom, DISTRICT_ZOOM + 0.5))}
            />
            </>
          )}
        </aside>

        {/* ------------------------------------------------------ map --- */}
        <section className="relative order-1 h-[40vh] min-w-0 shrink-0 md:order-2 md:h-auto md:flex-1 md:shrink">
          <NationalMap
            sku={sku}
            refreshKey={refreshKey}
            siloStates={siloStates}
            outbreakDistricts={outbreakMarks}
            rowsScope={rowsScope(user)}
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

          <div className="absolute top-3 right-3 z-[900] flex flex-col items-end gap-1.5">
            <div className="flex items-center gap-2">
              {canRunEmergency && !loopFor && (
                <button
                  onClick={startEmergency}
                  disabled={emergencyBusy}
                  title={`Declares a scripted acute diarrhoeal outbreak in ${SANDBOX.label} and carries it through the surge, early warnings, pre-positioning, Gemini, the donor's yes, dispatch and receipt. Writes stay in the sandbox; the plans it runs cover the outbreak's medicines across the state.`}
                  className="rounded-lg bg-crit px-3 py-1.5 text-[12px] font-semibold text-white shadow-sm hover:bg-crit/90 focus:ring-2 focus:ring-crit/30 focus:outline-none disabled:opacity-60"
                >
                  {emergencyBusy ? "Choosing a facility…" : "Simulate emergency"}
                </button>
              )}
              {view.tier !== "state" && (
                <button
                  onClick={goNational}
                  className="rounded-lg border border-line bg-panel px-3 py-1.5 text-[12px] font-medium text-ink shadow-sm hover:bg-canvas"
                >
                  Back to all of India
                </button>
              )}
            </div>
            {emergencyError && (
              <p role="alert" className="max-w-[280px] rounded-md bg-panel px-2 py-1 text-[11.5px] text-crit shadow-sm">
                {emergencyError}
              </p>
            )}
          </div>

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

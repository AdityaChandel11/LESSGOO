import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The API lives at /api on the same origin as the site: served by the same
 * container in production, proxied by Vite in development. VITE_API_ORIGIN is
 * only for the unusual case of an API on a different origin.
 */
const BASE = `${import.meta.env.VITE_API_ORIGIN ?? ""}/api`;

/** Fired when any request finds the session gone, so the app returns to sign-in. */
export const SESSION_EXPIRED_EVENT = "swasthsetu:session-expired";

export type Status = "healthy" | "at_risk" | "critical";

export interface Sku {
  code: string;
  name: string;
  unit: string;
  is_controlled: boolean;
  cold_chain: boolean;
}

/** A state or district rollup. */
export interface Bucket {
  key: string;
  label: string;
  lat: number;
  lng: number;
  total: number;
  critical: number;
  at_risk: number;
  healthy: number;
  min_days: number | null;
  critical_pct: number;
  status: Status;
  zoom: number;
  parent: string | null;
}

/** One facility as the map and lists need it — no per-medicine detail. */
export interface Pin {
  id: string;
  name: string;
  type: string;
  district: string;
  state_silo: string;
  lat: number;
  lng: number;
  status: Status;
  min_days: number | null;
  critical_skus: number;
  at_risk_skus: number;
}

export interface Summary {
  facilities: number;
  critical: number;
  at_risk: number;
  healthy: number;
  min_days: number | null;
  states: number;
  districts: number;
  chcs: number;
  phcs: number;
  sku: string | null;
  state: string | null;
  state_name: string | null;
}

export interface SkuStock {
  sku_code: string;
  sku_name: string;
  qty_on_hand: number;
  daily_burn_rate: number | null;
  days_of_stock: number | null;
  /** Which rule produced the rate: the last 28 days of readings, or the
   *  federated model's published forecast. */
  rate_source: "burn_rate" | "federated";
  status: Status;
  is_controlled: boolean;
  cold_chain: boolean;
  last_reported_at: string | null;
  last_source: string | null;
  last_confidence: number | null;
}

export interface FacilityDetail {
  id: string;
  name: string;
  type: string;
  district: string;
  state_silo: string;
  lat: number;
  lng: number;
  status: Status;
  escalated: boolean;
  escalation_reasons: string[];
  beds_total: number;
  beds_occupied: number | null;
  bed_occupancy_pct: number | null;
  staff_checkin_pct: number | null;
  trust_score: number | null;
  trust_band: "good" | "watch" | "audit" | null;
  warning_multiplier: number;
  skus: SkuStock[];
}

export interface Health {
  database: boolean;
  environment: string;
  demo_mode: boolean;
  modes: { llm: string; maps: string; comms: string };
}

export type Role = "admin" | "state_officer" | "block_mo" | "facility_user";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  state_silo: string | null;
  district: string | null;
  facility_id: string | null;
}

export interface Session {
  user: User;
  demo_mode: boolean;
  environment: string;
}

type Params = Record<string, string | number | null | undefined>;

/** Error carrying the API's own explanation, e.g. a stale-plan refusal. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  method: "GET" | "POST",
  path: string,
  { params = {}, body, signal }: { params?: Params; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") qs.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}${qs.size ? `?${qs}` : ""}`, {
      method,
      signal,
      credentials: "include",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the server. Check your connection.");
  }

  if (res.status === 401 && path !== "/auth/login" && path !== "/auth/me") {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      // keep the generic message
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const get = <T>(path: string, params: Params = {}, signal?: AbortSignal) =>
  request<T>("GET", path, { params, signal });
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, { body });

/**
 * Mirrors the server's access policy (backend/app/auth.py) so the interface
 * only offers actions a person can actually take. The server still decides:
 * hiding a button is a courtesy, never the protection.
 */
export const can = {
  planState: (u: User, state: string) =>
    u.role === "admin" || (u.role === "state_officer" && u.state_silo === state),
  decideTransfer: (
    u: User,
    from: { state: string; district: string },
    to: { state: string; district: string },
  ) => {
    if (u.role === "admin") return true;
    if (u.role === "state_officer") return u.state_silo === from.state && from.state === to.state;
    if (u.role === "block_mo")
      return (
        u.state_silo === from.state &&
        from.state === to.state &&
        u.district === from.district &&
        from.district === to.district
      );
    return false;
  },
  report: (u: User, f: { id: string; state_silo: string; district: string }) => {
    if (u.role === "admin") return true;
    if (u.role === "state_officer") return u.state_silo === f.state_silo;
    if (u.role === "block_mo") return u.state_silo === f.state_silo && u.district === f.district;
    return u.facility_id === f.id;
  },
};

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  state_officer: "State officer",
  block_mo: "District officer",
  facility_user: "Facility staff",
};

export interface DemoAccount {
  email: string;
  name: string;
  role: Role;
  scope: string;
}

export const auth = {
  me: () => get<Session>("/auth/me"),
  login: (email: string, password: string) => post<Session>("/auth/login", { email, password }),
  logout: () => post<void>("/auth/logout"),
  demoAccounts: () => get<DemoAccount[]>("/auth/demo-accounts"),
  demoLogin: (email: string) => post<Session>("/auth/demo", { email }),
};

export interface ClientConfig {
  maps_mode: "osm" | "google";
  maps_browser_key: string;
  demo_mode: boolean;
}

export const api = {
  health: () => get<Health>("/health"),
  clientConfig: () => get<ClientConfig>("/client-config"),
  skus: () => get<Sku[]>("/skus"),
  summary: (sku: string | null, state?: string | null) =>
    get<Summary>("/map/summary", { sku, state }),
  states: (sku: string | null) => get<Bucket[]>("/map/states", { sku }),
  districts: (sku: string | null, state?: string | null) =>
    get<Bucket[]>("/map/districts", { sku, state }),
  pinsInView: (
    bounds: { south: number; west: number; north: number; east: number },
    sku: string | null,
    signal?: AbortSignal,
  ) => get<Pin[]>("/map/facilities", { ...bounds, sku, limit: 2500 }, signal),
  pinsInState: (state: string, sku: string | null, limit = 400) =>
    get<Pin[]>("/map/facilities", { state, sku, limit }),
  facility: (id: string) => get<FacilityDetail>(`/facilities/${encodeURIComponent(id)}`),
  transfers: (state: string) =>
    get<Transfer[]>("/transfers", {
      state,
      status: "proposed,approved,rejected",
      limit: 1000,
    }),
  plan: (state: string, sku: string | null) =>
    post<Plan>("/transfers/plan", { state, sku }),
  decide: (id: number, decision: "approve" | "reject") =>
    post<Transfer>(`/transfers/${id}/${decision}`),
  movements: (q: {
    state?: string | null;
    view: MovementView;
    sku?: string | null;
    facility?: string | null;
    limit?: number;
  }) =>
    get<Movements>("/movements", { ...q, limit: q.limit ?? 100 }),
  confirmReceipt: (id: number, qty_received: number, note?: string) =>
    post<Receipt>(`/movements/${id}/receipt`, { qty_received, note, via: "form" }),
  bedCode: (facilityId: string) =>
    get<BedCode>(`/facilities/${encodeURIComponent(facilityId)}/bed-code`),
  bedReports: (facilityId: string, limit = 8) =>
    get<BedReport[]>(`/facilities/${encodeURIComponent(facilityId)}/bed-reports`, { limit }),
  submitBedReport: (facilityId: string, body: Record<string, unknown>) =>
    post<BedReport>(`/facilities/${encodeURIComponent(facilityId)}/bed-reports`, body),
  attendance: (facilityId: string) =>
    get<Attendance>(`/facilities/${encodeURIComponent(facilityId)}/attendance`),
  checkin: (facilityId: string, body: Record<string, unknown>) =>
    post<Checkin>(`/facilities/${encodeURIComponent(facilityId)}/checkins`, body),
  facilityTrust: (facilityId: string) =>
    get<Trust | null>(`/facilities/${encodeURIComponent(facilityId)}/trust`),
  trustQueue: (limit = 50) => get<AuditRow[]>("/trust/queue", { limit }),
};

/* --------------------------------------------------------- attendance --- */

export interface Attendance {
  facility_id: string;
  roster: number;
  present: number;
  rate: number | null;
  /** How each present staff member was located: gps / cell_id / simulated / none. */
  by_method: Record<string, number>;
  geofence_pass: number;
  geofence_checked: number;
  footfall_today: number | null;
  /** Set when staff are present and no patients were logged — worth a look, no more. */
  contradiction: string | null;
}

export interface Checkin {
  facility_id: string;
  action: string;
  shift: string | null;
  source: string | null;
  loc_method: string | null;
  geofence_km: number | null;
  geofence_ok: boolean | null;
  checked_in_at: string;
  checked_out_at: string | null;
}

/* -------------------------------------------------------- trust layer --- */

export interface TrustComponent {
  signal: string;
  penalty: number;
  weight: number;
  cost: number;
  reason: string;
  /** The rows this sentence was computed from — the same filter the movement
   *  tab uses, so the link opens exactly what the score counted. */
  evidence: { tab: string; facility?: string; view?: MovementView } | null;
}

export interface Trust {
  facility_id: string;
  score: number;
  band: "good" | "watch" | "audit";
  components: TrustComponent[];
  computed_at: string;
  /** How much earlier this facility's stock warning trips (spec 12.6). */
  warning_multiplier: number;
}

export interface AuditRow extends Trust {
  facility_name: string;
  type: string;
  district: string;
  state_silo: string;
  lat: number;
  lng: number;
}

export const TRUST_BAND: Record<string, { label: string; className: string; dot: string }> = {
  good: { label: "Consistent", className: "text-ok bg-ok/10 border-ok/30", dot: "#1b9150" },
  watch: { label: "Worth a look", className: "text-risk bg-risk/10 border-risk/30", dot: "#e0900e" },
  audit: { label: "Visit first", className: "text-crit bg-crit/10 border-crit/30", dot: "#d92d20" },
};

export const SIGNAL_LABEL: Record<string, string> = {
  attendance_vs_footfall: "Attendance vs patients seen",
  consumption_vs_footfall: "Medicine use vs patients seen",
  beds_vs_register: "Ward photo vs admission register",
  receipt_discipline: "Deliveries confirmed",
  implausible_smoothness: "Figures vary like a real storeroom",
  verification_quality: "Reports that can be checked",
};

/* --------------------------------------------------------- bed capture --- */

export interface BedCode {
  facility_id: string;
  for_date: string;
  code: string;
  delivered_at: string | null;
  delivery: string;
}

export interface BedReport {
  id: number;
  facility_id: string;
  ward: string;
  beds_total: number | null;
  beds_occupied: number | null;
  reported_at: string;
  source: string;
  verification: "verified" | "unverified" | "rejected";
  code_ok: boolean | null;
  code_read: string | null;
  geofence_ok: boolean | null;
  geofence_km: number | null;
  loc_method: string | null;
  register_admissions: number | null;
  model_confidence: number | null;
  /** "mock" when no photograph was analysed — shown, never hidden. */
  model: string | null;
  reasons: string[];
}

/* --------------------------------------------------------- movements --- */

/** Settled states, plus the two the clock derives (spec 26.3). */
export type MovementStatus =
  | "in_transit"
  | "overdue"
  | "received"
  | "short"
  | "over"
  | "cancelled";
export type MovementView = MovementStatus | "attention" | "all";

export interface Movement {
  id: number;
  batch_id: string;
  sku_code: string;
  sku_name: string;
  unit: string;
  from_ref: string;
  to_facility: string;
  facility_name: string;
  state_silo: string;
  district: string;
  qty_dispatched: number;
  dispatched_at: string;
  dispatch_source: "warehouse" | "transfer" | "seed";
  transfer_id: number | null;
  expected_by: string;
  qty_received: number | null;
  received_at: string | null;
  received_via: string | null;
  status: MovementStatus;
  discrepancy_qty: number | null;
  days_outstanding: number | null;
  note: string | null;
}

export interface Movements {
  view: string;
  counts: Record<string, number>;
  short_units: number;
  movements: Movement[];
}

export interface Receipt {
  movement: Movement;
  status_before: string;
  status_after: string;
  status_changed: boolean;
  qty_on_hand: number;
}

export const MOVEMENT_LABEL: Record<MovementStatus, string> = {
  in_transit: "In transit",
  overdue: "Not confirmed",
  received: "Received",
  short: "Short",
  over: "Over",
  cancelled: "Cancelled",
};

/** Colour carries the same meaning here as on the map: red needs someone. */
export const MOVEMENT_TONE: Record<MovementStatus, string> = {
  overdue: "text-crit bg-crit/10 border-crit/30",
  short: "text-crit bg-crit/10 border-crit/30",
  over: "text-risk bg-risk/10 border-risk/30",
  in_transit: "text-ink-2 bg-canvas border-line",
  received: "text-ok bg-ok/10 border-ok/30",
  cancelled: "text-ink-3 bg-canvas border-line",
};

/* --------------------------------------------------------- transfers --- */

export interface FacilityRef {
  id: string;
  name: string;
  district: string;
  lat: number;
  lng: number;
}

export interface TransferRationale {
  solver?: string;
  binding?: string;
  recipient_status?: Status;
  recipient_days_before?: number;
  recipient_days_after_this?: number;
  recipient_days_after_plan?: number;
  recipient_incoming_transfers?: number;
  recipient_target_days?: number;
  donor_days_before?: number;
  donor_days_after_this?: number;
  donor_days_after_plan?: number;
  donor_outgoing_transfers?: number;
  donor_floor_days?: number;
  distance_limit_km?: number;
  cold_chain?: boolean;
  distance_basis?: string;
}

export interface Transfer {
  id: number;
  status: "proposed" | "approved" | "rejected" | "completed";
  sku_code: string;
  sku_name: string;
  unit: string;
  cold_chain: boolean;
  qty: number;
  route_km: number;
  eta_hours: number;
  route_source: string | null;
  triggered_by: string | null;
  created_at: string;
  rationale: TransferRationale;
  from: FacilityRef;
  to: FacilityRef;
}

export interface Shortfall {
  facility_id: string;
  name: string;
  district: string;
  sku_code: string;
  sku_name: string;
  days: number | null;
  units_needed: number;
  reason: string;
}

export interface Plan {
  state: string;
  sku: string | null;
  solver: string;
  generated_at: string;
  totals: {
    transfers: number;
    units: number;
    facilities_helped: number;
    donor_facilities: number;
    facilities_still_short: number;
    total_km: number;
    manual_review: number;
  };
  transfers: Transfer[];
  unmet: Shortfall[];
  manual_review: Shortfall[];
}

export function submitReading(body: {
  facility_id: string;
  sku_code: string;
  qty_on_hand: number;
  source?: string;
  reporter_ref?: string;
  confidence?: number;
}) {
  return post<unknown>("/stock/readings", body);
}

/* ------------------------------------------------------------ realtime --- */

export interface LiveEvent {
  kind:
    | "reading.committed"
    | "status.changed"
    | "transfer.decided"
    | "transfer.proposed"
    | "movement.received";
  facility_id: string;
  facility_name: string;
  sku_code?: string;
  qty_on_hand?: number;
  source?: string;
  direction?: "in" | "out";
  days_of_stock?: number | null;
  reporter_ref?: string | null;
  from?: Status;
  to?: Status;
  // transfer.decided
  transfer_id?: number;
  decision?: "approved" | "rejected";
  qty?: number;
  from_name?: string;
  to_name?: string;
  to_days_after?: number | null;
  // transfer.proposed
  state?: string;
  transfers?: number;
  // movement.received
  movement_id?: number;
  batch_id?: string;
  sku_name?: string;
  qty_dispatched?: number;
  qty_received?: number;
  status?: string;
  received_via?: string;
  /** From the change being saved to this browser learning of it, including
   *  the wait for the next poll. Measured on the server's clock, so a skewed
   *  laptop clock cannot distort it. */
  latencyMs: number;
  receivedAt: number;
  /** Monotonic arrival order. Timestamps can collide within a millisecond. */
  seq: number;
}

interface EventsResponse {
  cursor: number;
  reset: boolean;
  server_time: string;
  events: { id: number; kind: LiveEvent["kind"]; created_at: string; data: Record<string, unknown> }[];
}

export const POLL_VISIBLE_MS = 4000;
const POLL_HIDDEN_MS = 30000;
const MAX_BACKOFF_MS = 60000;

let eventSeq = 0;

/**
 * Live updates by polling. Every few seconds the browser asks the server for
 * changes after the last one it saw. Works through any proxy or CDN and across
 * any number of server instances; see backend/app/events.py.
 *
 * Returns `resyncs`, which increments when the server says this browser has
 * fallen too far behind to catch up event by event and should reload its data.
 */
export function useLiveUpdates() {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [resyncs, setResyncs] = useState(0);
  const pollNowRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    let cursor: number | null = null;
    let timer: number | undefined;
    let failures = 0;
    let inFlight = false;
    let stopped = false;

    const schedule = (ms: number) => {
      window.clearTimeout(timer);
      if (!stopped) timer = window.setTimeout(tick, ms);
    };

    async function tick() {
      if (inFlight || stopped) return;
      inFlight = true;
      const started = performance.now();
      let next = POLL_VISIBLE_MS;
      try {
        const res = await get<EventsResponse>("/events", { after: cursor });
        const roundTrip = performance.now() - started;
        failures = 0;
        setConnected(true);
        if (res.reset) setResyncs((n) => n + 1);
        if (res.events.length) {
          const serverNow = Date.parse(res.server_time);
          const now = Date.now();
          const fresh = res.events.map((e) => ({
            ...(e.data as object),
            kind: e.kind,
            latencyMs: Math.max(0, Math.round(serverNow - Date.parse(e.created_at) + roundTrip)),
            receivedAt: now,
            seq: ++eventSeq,
          })) as LiveEvent[];
          // Server sends oldest first; the app wants newest first.
          setEvents((prev) => [...fresh.reverse(), ...prev].slice(0, 30));
        }
        cursor = res.cursor;
        next = document.hidden ? POLL_HIDDEN_MS : POLL_VISIBLE_MS;
      } catch (e) {
        setConnected(false);
        if (e instanceof ApiError && e.status === 401) {
          // The session is gone; the sign-in screen takes over.
          stopped = true;
          return;
        }
        failures += 1;
        next = Math.min(POLL_VISIBLE_MS * 2 ** failures, MAX_BACKOFF_MS);
      } finally {
        inFlight = false;
        schedule(next);
      }
    }

    const pollNow = () => {
      if (!inFlight) schedule(0);
    };
    pollNowRef.current = pollNow;

    const onVisible = () => {
      if (!document.hidden) pollNow();
    };
    document.addEventListener("visibilitychange", onVisible);
    tick();

    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  /** Check for changes immediately, e.g. right after this user saves one. */
  const pollNow = useCallback(() => pollNowRef.current(), []);

  return { connected, events, resyncs, pollNow };
}

/* ------------------------------------------------------------ display --- */

export const STATUS_COLOR: Record<Status, string> = {
  critical: "#d92d20",
  at_risk: "#e0900e",
  healthy: "#1b9150",
};

export const STATUS_LABEL: Record<Status, string> = {
  critical: "Critical",
  at_risk: "At risk",
  healthy: "Adequate",
};

export const STATUS_RULE: Record<Status, string> = {
  critical: "under 3 days of stock",
  at_risk: "3 to 7 days of stock",
  healthy: "7 days or more",
};

/**
 * Severity for a rollup. "Any facility critical" would paint every state red
 * and tell an officer nothing, so aggregates are graded by the *share* of
 * facilities in trouble instead.
 */
export function bucketSeverity(b: Pick<Bucket, "total" | "critical" | "at_risk">): Status {
  if (!b.total) return "healthy";
  const crit = b.critical / b.total;
  const trouble = (b.critical + b.at_risk) / b.total;
  if (crit >= 0.2) return "critical";
  if (crit >= 0.08 || trouble >= 0.5) return "at_risk";
  return "healthy";
}

export function formatDays(d: number | null | undefined): string {
  if (d === null || d === undefined) return "—";
  if (d < 1) return "<1 day";
  if (d >= 60) return "60+ days";
  return `${d.toFixed(d < 10 ? 1 : 0)} days`;
}

export function formatLatency(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function pct(n: number, total: number): string {
  return total ? `${Math.round((100 * n) / total)}%` : "0%";
}

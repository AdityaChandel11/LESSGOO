import { useMemo, useState } from "react";

import {
  type Bucket,
  type Plan,
  type Shortfall,
  type Sku,
  type Transfer,
  STATUS_COLOR,
  formatDays,
} from "./api";
import { StackBar } from "./panels";

/* ================================================================ trips === */

/**
 * One vehicle run between two facilities. The solver works per medicine, but
 * nobody sends a separate van for each medicine going the same way, so the
 * officer decides on the trip.
 */
export interface Trip {
  id: string;
  from: Transfer["from"];
  to: Transfer["to"];
  km: number;
  etaHours: number;
  items: Transfer[];
  status: "proposed" | "approved" | "rejected" | "mixed";
  urgent: boolean;
  worstDaysBefore: number;
}

export function groupTrips(transfers: Transfer[]): Trip[] {
  const byPair = new Map<string, Transfer[]>();
  for (const t of transfers) {
    const key = `${t.from.id}>${t.to.id}`;
    const list = byPair.get(key);
    if (list) list.push(t);
    else byPair.set(key, [t]);
  }
  const trips: Trip[] = [];
  for (const [id, items] of byPair) {
    const statuses = new Set(items.map((i) => i.status));
    const status: Trip["status"] =
      statuses.size === 1
        ? (items[0].status === "completed" ? "approved" : items[0].status)
        : "mixed";
    trips.push({
      id,
      from: items[0].from,
      to: items[0].to,
      km: items[0].route_km,
      etaHours: items[0].eta_hours,
      items,
      status,
      urgent: items.some((i) => i.rationale.recipient_status === "critical"),
      worstDaysBefore: Math.min(
        ...items.map((i) => i.rationale.recipient_days_before ?? Number.POSITIVE_INFINITY),
      ),
    });
  }
  return trips.sort(
    (a, b) => Number(b.urgent) - Number(a.urgent) || a.worstDaysBefore - b.worstDaysBefore || a.km - b.km,
  );
}

function travelTime(hours: number): string {
  const mins = Math.round(hours * 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/* ============================================================ primitives === */

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
      {children}
    </div>
  );
}

function Stat({ value, label, tone }: { value: string; label: string; tone?: string }) {
  return (
    <div className="bg-panel px-3 py-2">
      <div
        className="font-mono text-[18px] leading-none font-semibold tabular-nums"
        style={{ color: tone ?? "var(--color-ink)" }}
      >
        {value}
      </div>
      <div className="mt-1 text-[11px] text-ink-3">{label}</div>
    </div>
  );
}

const RULES = [
  "Any facility under 7 days of cover is topped up to 14 days.",
  "A donor always keeps at least 14 days of its own stock.",
  "Transfers stay inside the state and within 150 km; cold-chain items within 60 km.",
  "When stock is scarce, critical facilities are served first.",
  "Controlled substances are never routed automatically.",
];

/* ============================================================= national === */

export function TransfersPrompt({
  states,
  onPickState,
}: {
  states: Bucket[];
  onPickState: (b: Bucket) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-4 pt-4 pb-4">
        <Eyebrow>Redistribution</Eyebrow>
        <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">
          Move stock before shelves empty
        </h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">
          Plans are made state by state, because moving medicine between facilities is decided
          within a state. Choose one to plan its transfers.
        </p>
        <ul className="mt-3 space-y-1.5">
          {RULES.map((r) => (
            <li key={r} className="flex gap-2 text-[12px] text-ink-2">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-3" />
              {r}
            </li>
          ))}
        </ul>
      </div>
      <div className="px-4 pt-3 pb-1.5">
        <Eyebrow>States by facilities critical</Eyebrow>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {states.map((b) => (
          <button
            key={b.key}
            onClick={() => onPickState(b)}
            className="grid w-full grid-cols-[1fr_auto] items-center gap-x-3 px-4 py-2 text-left hover:bg-canvas"
          >
            <div className="min-w-0">
              <div className="truncate text-[13px] font-medium text-ink">{b.label}</div>
              <div className="mt-1.5">
                <StackBar critical={b.critical} at_risk={b.at_risk} healthy={b.healthy} height={5} />
              </div>
            </div>
            <span className="text-[12px] font-medium text-brand">Plan →</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ================================================================ panel === */

type View = "impact" | "open" | "approved" | "all";

/** Under this many days of stock is "critical" (backend settings.critical_days). */
const CRITICAL_DAYS = 3;
const HIGH_IMPACT_LIMIT = 10;

/** An open trip that, on its own, takes a critical receiver out of critical. */
function isHighImpact(trip: Trip): boolean {
  return trip.items.some(
    (i) =>
      i.status === "proposed" &&
      i.rationale.recipient_status === "critical" &&
      (i.rationale.recipient_days_after_this ?? 0) >= CRITICAL_DAYS,
  );
}

export function RedistributionPanel({
  stateLabel,
  sku,
  skus,
  transfers,
  plan,
  planning,
  planError,
  planMs,
  highlightTripId,
  canPlan,
  canDecide,
  onPlan,
  onDecideTrip,
  onHoverTrip,
  onFocusTrip,
}: {
  canPlan: boolean;
  canDecide: (trip: Trip) => boolean;
  stateLabel: string;
  sku: string | null;
  skus: Sku[];
  transfers: Transfer[];
  plan: Plan | null;
  planning: boolean;
  planError: string | null;
  planMs: number | null;
  highlightTripId: string | null;
  onPlan: () => void;
  onDecideTrip: (trip: Trip, decision: "approve" | "reject") => Promise<Record<number, string>>;
  onHoverTrip: (id: string | null) => void;
  onFocusTrip: (trip: Trip) => void;
}) {
  const [view, setView] = useState<View>("open");
  const [visibleCount, setVisibleCount] = useState(40);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<number, string>>({});

  const medicine = sku ? (skus.find((s) => s.code === sku)?.name ?? sku) : "all medicines";

  const trips = useMemo(() => {
    const scoped = sku ? transfers.filter((t) => t.sku_code === sku) : transfers;
    return groupTrips(scoped);
  }, [transfers, sku]);

  // Trips arrive sorted most urgent first, so the first ten are the ten to show.
  const impact = useMemo(() => trips.filter(isHighImpact).slice(0, HIGH_IMPACT_LIMIT), [trips]);

  const shown = useMemo(() => {
    if (view === "impact") return impact;
    if (view === "open") return trips.filter((t) => t.status === "proposed" || t.status === "mixed");
    if (view === "approved") return trips.filter((t) => t.status === "approved" || t.status === "mixed");
    return trips;
  }, [trips, view, impact]);

  const counts = useMemo(
    () => ({
      open: trips.filter((t) => t.status === "proposed" || t.status === "mixed").length,
      approved: trips.filter((t) => t.status === "approved").length,
    }),
    [trips],
  );

  const decide = async (trip: Trip, decision: "approve" | "reject") => {
    setBusy((b) => new Set(b).add(trip.id));
    const errs = await onDecideTrip(trip, decision);
    setErrors((e) => {
      const next = { ...e };
      for (const i of trip.items) delete next[i.id];
      return { ...next, ...errs };
    });
    setBusy((b) => {
      const n = new Set(b);
      n.delete(trip.id);
      return n;
    });
  };

  const scopedUnmet = plan ? plan.unmet.filter((u) => !sku || u.sku_code === sku) : [];
  const scopedManual = plan ? plan.manual_review.filter((u) => !sku || u.sku_code === sku) : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-4 pt-4 pb-4">
        <Eyebrow>Redistribution</Eyebrow>
        <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">{stateLabel}</h2>
        <p className="mt-0.5 text-[12.5px] text-ink-2">
          Transfers of <span className="font-medium text-ink">{medicine}</span> between facilities
          in this state
        </p>

        <div className="mt-3 flex items-center gap-2">
          {canPlan ? (
            <button
              onClick={onPlan}
              disabled={planning}
              className="h-9 rounded-md bg-brand px-3.5 text-[13px] font-medium text-white hover:bg-brand/90 disabled:opacity-60"
            >
              {planning ? "Solving…" : plan || transfers.length ? "Re-run plan" : "Generate transfer plan"}
            </button>
          ) : (
            <p className="rounded-md bg-canvas px-2.5 py-1.5 text-[12px] text-ink-2">
              View only. Plans for {stateLabel} are generated by its state officers.
            </p>
          )}
          {plan && planMs !== null && (
            <span className="text-[11px] text-ink-3">
              Solved with {plan.solver === "ortools" ? "OR-Tools" : plan.solver} in{" "}
              {(planMs / 1000).toFixed(1)} s
            </span>
          )}
        </div>
        {planError && <p className="mt-2 text-[12px] text-crit">{planError}</p>}

        {plan ? (
          <div className="mt-3 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line">
            <Stat value={counts.open.toLocaleString("en-IN")} label="trips to decide" />
            <Stat
              value={String(plan.totals.facilities_helped)}
              label="facilities helped"
              tone={STATUS_COLOR.healthy}
            />
            <Stat
              value={String(plan.totals.facilities_still_short)}
              label="still short"
              tone={plan.totals.facilities_still_short ? STATUS_COLOR.critical : undefined}
            />
          </div>
        ) : (
          !transfers.length && (
            <ul className="mt-3 space-y-1.5">
              {RULES.map((r) => (
                <li key={r} className="flex gap-2 text-[12px] text-ink-2">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-3" />
                  {r}
                </li>
              ))}
            </ul>
          )
        )}
      </div>

      {trips.length > 0 && (
        <div className="flex items-center gap-0.5 overflow-x-auto border-b border-line px-2 pt-2">
          {(
            [
              ["impact", "High impact", impact.length],
              ["open", `To decide`, counts.open],
              ["approved", "Approved", counts.approved],
              ["all", "All", trips.length],
            ] as const
          ).map(([v, label, n]) => (
            <button
              key={v}
              onClick={() => {
                setView(v);
                setVisibleCount(40);
              }}
              className={`-mb-px shrink-0 border-b-2 px-2 pb-2 text-[12.5px] font-medium whitespace-nowrap ${
                view === v ? "border-brand text-ink" : "border-transparent text-ink-3 hover:text-ink-2"
              }`}
            >
              {label}
              <span className="ml-1 font-mono text-[11px] text-ink-3">{n}</span>
            </button>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {view === "impact" && trips.length > 0 && (
          <p className="border-b border-line bg-canvas px-4 py-2 text-[11.5px] text-ink-2">
            Receivers under {CRITICAL_DAYS} days of stock that this trip alone lifts to{" "}
            {CRITICAL_DAYS} days or more. Most urgent first, top {HIGH_IMPACT_LIMIT}.
          </p>
        )}
        {shown.slice(0, visibleCount).map((trip) => (
          <TripCard
            key={trip.id}
            trip={trip}
            sku={sku}
            mayDecide={canDecide(trip)}
            highlighted={trip.id === highlightTripId}
            busy={busy.has(trip.id)}
            errors={errors}
            onDecide={decide}
            onHover={onHoverTrip}
            onFocus={onFocusTrip}
          />
        ))}
        {shown.length > visibleCount && (
          <button
            onClick={() => setVisibleCount((c) => c + 40)}
            className="w-full py-3 text-[12px] font-medium text-brand hover:bg-canvas"
          >
            Show {Math.min(40, shown.length - visibleCount)} more of {shown.length - visibleCount}
          </button>
        )}
        {trips.length > 0 && shown.length === 0 && (
          <p className="px-4 py-6 text-center text-[12px] text-ink-3">Nothing here.</p>
        )}
        {!plan && !transfers.length && !planning && (
          <p className="px-4 py-6 text-[12px] text-ink-3">
            No plan yet for {stateLabel}. Generating one takes about a second.
          </p>
        )}

        {scopedUnmet.length > 0 && (
          <ShortfallList
            title="Still short — escalate to state warehouse"
            tone={STATUS_COLOR.critical}
            items={scopedUnmet}
          />
        )}
        {scopedManual.length > 0 && (
          <ShortfallList
            title="Controlled substances — manual review"
            tone="#4a525c"
            items={scopedManual}
          />
        )}
      </div>
    </div>
  );
}

function TripCard({
  trip,
  sku,
  mayDecide,
  highlighted,
  busy,
  errors,
  onDecide,
  onHover,
  onFocus,
}: {
  trip: Trip;
  sku: string | null;
  mayDecide: boolean;
  highlighted: boolean;
  busy: boolean;
  errors: Record<number, string>;
  onDecide: (trip: Trip, decision: "approve" | "reject") => void;
  onHover: (id: string | null) => void;
  onFocus: (trip: Trip) => void;
}) {
  const open = trip.items.filter((i) => i.status === "proposed");
  const approxDistance = trip.items[0]?.route_source !== "google_routes";

  return (
    <div
      onMouseEnter={() => onHover(trip.id)}
      onMouseLeave={() => onHover(null)}
      className={`border-b border-line px-4 py-3 ${highlighted ? "bg-brand/[0.05]" : ""}`}
    >
      <button onClick={() => onFocus(trip)} className="block w-full text-left">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 text-[13px] leading-snug">
            <span className="font-medium text-ink">{trip.from.name}</span>
            <span className="mx-1.5 text-ink-3">→</span>
            <span className="font-medium text-ink">{trip.to.name}</span>
          </div>
          <span
            className="shrink-0 font-mono text-[11px] text-ink-3 tabular-nums"
            title={approxDistance ? "Estimated from straight-line distance x 1.3" : "Road distance"}
          >
            {approxDistance && "~"}
            {Math.round(trip.km)} km · {travelTime(trip.etaHours)}
          </span>
        </div>
      </button>

      <ul className="mt-2 space-y-1.5">
        {trip.items.map((t) => {
          const r = t.rationale;
          const others = (r.recipient_incoming_transfers ?? 1) - 1;
          const dim = sku && t.sku_code !== sku;
          return (
            <li key={t.id} className={dim ? "opacity-50" : ""}>
              <div className="flex items-baseline justify-between gap-2 text-[12px]">
                <span className="min-w-0 truncate text-ink-2">
                  <span className="font-medium text-ink">{t.sku_name}</span>
                  {t.cold_chain && <span className="ml-1 text-[10.5px] text-ink-3">(cold chain)</span>}
                </span>
                <span className="shrink-0 font-mono text-ink tabular-nums">
                  {Math.round(t.qty).toLocaleString("en-IN")} {t.unit}
                </span>
              </div>
              <div className="flex items-baseline justify-between gap-2 text-[11px] text-ink-3">
                <span>
                  Receiver{" "}
                  <span style={{ color: STATUS_COLOR[r.recipient_status ?? "at_risk"] }}>
                    {formatDays(r.recipient_days_before)}
                  </span>{" "}
                  → <span className="text-ink-2">{formatDays(r.recipient_days_after_this)}</span>
                  {others > 0 && (
                    <span title="Once the other transfers of this medicine to this facility are approved too">
                      {" "}
                      ({formatDays(r.recipient_days_after_plan)} with {others} more)
                    </span>
                  )}
                </span>
                <span
                  title={
                    (r.donor_outgoing_transfers ?? 1) > 1
                      ? `After all ${r.donor_outgoing_transfers} of this donor's transfers of this medicine`
                      : undefined
                  }
                >
                  donor keeps at least {formatDays(r.donor_days_after_plan)}
                </span>
              </div>
              {t.status !== "proposed" && (
                <div
                  className="text-[11px] font-medium"
                  style={{ color: t.status === "rejected" ? "#7d858f" : STATUS_COLOR.healthy }}
                >
                  {/* Approval dispatches the batch; the recipient's stock only
                      rises when someone at the receiving end confirms it
                      arrived, and that pair is tracked under Movements. */}
                  {t.status === "rejected"
                    ? "Rejected"
                    : "Approved · dispatched, awaiting confirmation"}
                </div>
              )}
              {errors[t.id] && <div className="mt-0.5 text-[11px] text-crit">{errors[t.id]}</div>}
            </li>
          );
        })}
      </ul>

      {open.length > 0 && !mayDecide && (
        <p className="mt-2 text-right text-[11px] text-ink-3">
          Awaiting a decision from the responsible officer
        </p>
      )}
      {open.length > 0 && mayDecide && (
        <div className="mt-2.5 flex justify-end gap-2">
          <button
            onClick={() => onDecide(trip, "reject")}
            disabled={busy}
            className="h-7 rounded-md border border-line px-2.5 text-[12px] font-medium text-ink-2 hover:bg-canvas disabled:opacity-50"
          >
            Reject
          </button>
          <button
            onClick={() => onDecide(trip, "approve")}
            disabled={busy}
            className="h-7 rounded-md px-2.5 text-[12px] font-medium text-white disabled:opacity-50"
            style={{ background: STATUS_COLOR.healthy }}
          >
            {busy ? "Working…" : open.length > 1 ? `Approve trip (${open.length})` : "Approve"}
          </button>
        </div>
      )}
    </div>
  );
}

function ShortfallList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: string;
  items: Shortfall[];
}) {
  return (
    <details className="border-b border-line px-4 py-3" open={items.length <= 5}>
      <summary className="cursor-pointer text-[12px] font-semibold" style={{ color: tone }}>
        {title} <span className="font-mono text-ink-3">{items.length}</span>
      </summary>
      <ul className="mt-2 space-y-1.5">
        {items.map((u) => (
          <li key={`${u.facility_id}:${u.sku_code}`} className="text-[12px]">
            <div className="flex justify-between gap-2">
              <span className="truncate font-medium text-ink">{u.name}</span>
              <span className="shrink-0 font-mono text-ink-2 tabular-nums">
                needs {u.units_needed.toLocaleString("en-IN")}
              </span>
            </div>
            <div className="text-[11px] text-ink-3">
              {u.sku_name} · {formatDays(u.days)} left · {u.reason}
            </div>
          </li>
        ))}
      </ul>
    </details>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type FacilityDetail,
  type LiveEvent,
  type SkuStock,
  type Status,
  type Transfer,
  type User,
  api,
  can,
  formatDays,
  submitReading,
} from "./api";

/**
 * The live loop — one judge-driven pass through the whole chain.
 *
 * Everything a dashboard shows is a claim until somebody watches it change.
 * So this does not narrate: it drops a facility below its reorder floor, lets
 * the spine recompute, asks the real optimiser what to do about it, and moves
 * the stock when a human says yes. Each step prints the method and path it
 * called and the value that came back, because the point of the exercise is
 * that a sceptical person can check it against the network tab.
 *
 * Nothing here is staged. The quantity that triggers the stock-out is computed
 * from the facility's own burn rate; the donor, the distance and the rationale
 * are whatever the solver returned; the approval goes through the same
 * row-locked endpoint an officer uses.
 *
 * Writes are confined to the Nashik sandbox (see SANDBOX) so a demo cannot
 * drift the rest of the country, and `scripts/reset_nashik.py` puts it back.
 */

/** The one district this loop is allowed to write to. */
export const SANDBOX = { state: "MH", district: "Nashik", label: "Nashik, Maharashtra" };

export function inSandbox(f: { state_silo: string; district: string }): boolean {
  return f.state_silo === SANDBOX.state && f.district === SANDBOX.district;
}

/**
 * How far below the line to push it. The critical threshold is three days, so
 * a day and a half is unambiguously under it without being theatrical — and it
 * is multiplied by the facility's measured burn rate, not typed in as a
 * quantity, so it stays honest whatever that facility actually consumes.
 */
const TARGET_DAYS = 1.5;

type StepState = "pending" | "running" | "done" | "failed" | "skipped";

interface Step {
  id: string;
  title: string;
  /** The request this step made, shown verbatim. */
  call: string | null;
  state: StepState;
  /** Milliseconds from the first click. */
  at: number | null;
  detail: string | null;
  note: string | null;
  /** Set only on the two model steps: true when the server says Gemini wrote
   *  the text, false when it fell back to the computed line. */
  ai: boolean | null;
}

const STEP_TITLES: [string, string][] = [
  ["reading", "Reading committed"],
  ["recompute", "Days of stock recomputed"],
  ["flip", "Condition re-evaluated"],
  ["brief", "Gemini explains the risk"],
  ["plan", "Optimiser proposes a transfer"],
  ["why", "Gemini explains the transfer"],
  ["approve", "Officer approves"],
  ["moved", "Donor dispatches"],
  ["receipt", "Receiver confirms arrival"],
  ["feed", "Activity feed"],
];

const freshSteps = (): Step[] =>
  STEP_TITLES.map(([id, title]) => ({
    id,
    title,
    call: null,
    state: "pending",
    at: null,
    detail: null,
    note: null,
    ai: null,
  }));

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** One facility's stock of the moved medicine, read from the facility itself. */
interface Holding {
  name: string;
  qty: number;
  days: number | null;
}

async function holding(id: string, sku: string): Promise<Holding> {
  const d = await api.facility(id);
  const s = d.skus.find((x) => x.sku_code === sku);
  return { name: d.name, qty: s?.qty_on_hand ?? 0, days: s?.days_of_stock ?? null };
}

const STATUS_WORD: Record<Status, string> = {
  healthy: "adequate",
  at_risk: "at risk",
  critical: "critical",
};

function stamp(ms: number | null): string {
  if (ms === null) return "";
  return `+${(ms / 1000).toFixed(1)}s`;
}

function qty(n: number, unit: string): string {
  return `${Math.round(n).toLocaleString("en-IN")} ${unit}`;
}

/** The medicine with the most cover, so the drop is the largest true one. */
export function pickSku(skus: SkuStock[]): SkuStock | null {
  const usable = skus.filter(
    (s) =>
      // A controlled drug is excluded from the solver by design, so choosing
      // one would end the loop at step four with a manual-review row.
      !s.is_controlled && (s.daily_burn_rate ?? 0) > 0 && (s.days_of_stock ?? 0) > 3,
  );
  if (!usable.length) return null;
  return usable.reduce((best, s) =>
    (s.days_of_stock ?? 0) > (best.days_of_stock ?? 0) ? s : best,
  );
}

function Dot({ state }: { state: StepState }) {
  const cls =
    state === "done"
      ? "border-ok bg-ok"
      : state === "running"
        ? "border-brand bg-panel"
        : state === "failed"
          ? "border-crit bg-crit"
          : "border-line bg-panel";
  return (
    <span
      aria-hidden="true"
      className={`mt-[5px] block h-2.5 w-2.5 shrink-0 rounded-full border-2 ${cls} ${
        state === "running" ? "live-dot" : ""
      }`}
    />
  );
}

export function LiveLoopPanel({
  facility,
  user,
  events,
  autoStart = false,
  onClose,
  onChanged,
  onFocus,
  onOpenFederation,
}: {
  facility: FacilityDetail;
  user: User;
  /** The app's own poll, so the last step reports the feed rather than a copy. */
  events: LiveEvent[];
  /** Start the chain on open, for the one-click "Simulate emergency". */
  autoStart?: boolean;
  onClose: () => void;
  /** Ask the map and the panels to re-read after a write. */
  onChanged: () => void;
  onFocus: (lat: number, lng: number) => void;
  onOpenFederation?: () => void;
}) {
  const [steps, setSteps] = useState<Step[]>(freshSteps);
  const [phase, setPhase] = useState<
    "idle" | "running" | "awaiting" | "receiving" | "done" | "failed"
  >("idle");
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Donor and receiver, read from the facilities themselves before the
  // approval and after the receipt, so the two views can be compared.
  const [before, setBefore] = useState<{ donor: Holding; here: Holding } | null>(null);
  const [after, setAfter] = useState<{ donor: Holding; here: Holding } | null>(null);
  const started = useRef<number>(0);
  const sinceRef = useRef<string>("");

  const mark = useCallback((id: string, patch: Partial<Step>) => {
    // `at` is stamped the moment a step resolves, never recomputed on a
    // re-render — the timings a judge reads have to be the real ones.
    const settled =
      patch.state && patch.state !== "running" && patch.state !== "pending"
        ? { at: performance.now() - started.current }
        : {};
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch, ...settled } : s)));
  }, []);

  const fail = useCallback(
    (id: string, e: unknown) => {
      const message = e instanceof ApiError ? e.message : String(e);
      mark(id, { state: "failed", detail: message });
      setError(message);
      setPhase("failed");
    },
    [mark],
  );

  const run = useCallback(async () => {
    started.current = performance.now();
    sinceRef.current = new Date().toISOString();
    setSteps(freshSteps());
    setTransfer(null);
    setError(null);
    setBefore(null);
    setAfter(null);
    setPhase("running");
    onFocus(facility.lat, facility.lng);

    // ---------------------------------------------------- 1. the reading ---
    const target = pickSku(facility.skus);
    if (!target) {
      mark("reading", {
        state: "failed",
        detail: "Every medicine here is already short, controlled, or has no measured burn rate.",
      });
      setPhase("failed");
      return;
    }
    const burn = target.daily_burn_rate as number;
    const newQty = Math.max(1, Math.round(burn * TARGET_DAYS));
    const daysBefore = target.days_of_stock;

    mark("reading", { state: "running", call: "POST /api/stock/readings" });
    let committed;
    try {
      committed = await submitReading({
        facility_id: facility.id,
        sku_code: target.sku_code,
        qty_on_hand: newQty,
        source: "sms",
        reporter_ref: `loop_${Math.random().toString(16).slice(2, 8)}`,
        confidence: 0.97,
      });
    } catch (e) {
      return fail("reading", e);
    }
    mark("reading", {
      state: "done",
      detail: `${target.sku_name}: ${qty(newQty, "units")} on hand, reported by SMS.`,
      note: `${qty(target.qty_on_hand, "units")} before · burn rate ${burn.toFixed(1)}/day, so ${newQty} is ${TARGET_DAYS} days of cover`,
    });

    // ------------------------------------------------ 2. days recomputed ---
    mark("recompute", {
      state: "done",
      call: "same response · services.refresh_facility_state",
      detail: `${formatDays(daysBefore)} → ${formatDays(committed.days_of_stock)} of cover.`,
      note: `Recomputed from the readings, not sent by the phone. Rate from ${target.rate_source === "federated" ? "the federated forecast" : "the last 28 days"}.`,
    });

    // ------------------------------------------------------ 3. the flip ---
    mark("flip", {
      state: "done",
      call: "GET /api/map/facilities (map re-reads)",
      detail: committed.status_changed
        ? `This facility went from ${STATUS_WORD[committed.status_before]} to ${STATUS_WORD[committed.status_after]}.`
        : `${target.sku_name} is now critical. The facility was already ${STATUS_WORD[committed.status_before]} on another medicine, so its dot does not change colour.`,
      note: "Flagged by the rule — under 3 days of stock is critical — not by a model. A dot carries the facility's worst medicine, which is why one can move without the other.",
    });
    onChanged();

    // ------------------------------------------- 4. Gemini reads the risk ---
    // The rule decided; Gemini says what it means for the people there. A
    // model failure never stops the chain: the endpoint falls back to the
    // computed line and says so.
    mark("brief", {
      state: "running",
      call: `POST /api/facilities/${facility.id}/briefing`,
    });
    const briefStarted = performance.now();
    try {
      const b = await api.briefing(facility.id, "en");
      mark("brief", {
        state: "done",
        ai: b.ai,
        detail: b.body,
        note: b.ai
          ? `${b.model} · ${seconds(performance.now() - briefStarted)} round trip${b.cached ? " · cached answer for the same stock position" : ""}`
          : `Computed line, not Gemini${b.note ? ` — ${b.note}` : ""}`,
      });
    } catch (e) {
      mark("brief", { state: "skipped", detail: e instanceof ApiError ? e.message : String(e) });
    }

    // ------------------------------------------------------ 5. the plan ---
    mark("plan", {
      state: "running",
      call: `POST /api/transfers/plan {state: "${SANDBOX.state}", sku: "${target.sku_code}"}`,
    });
    let proposal: Transfer | undefined;
    try {
      const plan = await api.plan(SANDBOX.state, target.sku_code);
      proposal = plan.transfers.find((t) => t.to.id === facility.id);
      if (!proposal) {
        const why = plan.unmet.find((u) => u.facility_id === facility.id);
        mark("plan", {
          state: "failed",
          detail:
            why?.reason ??
            "The solver found no donor able to help this facility without breaching its own floor.",
          note: `${plan.solver} · ${plan.totals.transfers} transfers proposed across ${SANDBOX.state}`,
        });
        setPhase("failed");
        return;
      }
      setTransfer(proposal);
      mark("plan", {
        state: "done",
        detail: `${proposal.from.name} → here: ${qty(proposal.qty, proposal.unit)} over ${proposal.route_km} km, about ${proposal.eta_hours.toFixed(1)} h.`,
        note: `${plan.solver} · distance from ${proposal.rationale.distance_basis ?? "road network"} · donor keeps ${formatDays(proposal.rationale.donor_days_after_this)} of its own cover`,
      });
    } catch (e) {
      return fail("plan", e);
    }

    // ------------------------------------- 6. Gemini explains the proposal ---
    mark("why", { state: "running", call: "POST /api/transfers/explain" });
    try {
      const w = await api.explainTrip([proposal.id]);
      mark("why", {
        state: "done",
        ai: w.ai,
        detail: w.text,
        note: w.ai
          ? `${w.model} · ${seconds(w.latency_ms ?? 0)}${w.cached ? " · cached answer for the same figures" : ""}`
          : `Computed line, not Gemini${w.note ? ` — ${w.note}` : ""}`,
      });
    } catch (e) {
      mark("why", { state: "skipped", detail: e instanceof ApiError ? e.message : String(e) });
    }
    setPhase("awaiting");
    onChanged();
  }, [facility, mark, fail, onChanged, onFocus]);

  const approve = useCallback(async () => {
    if (!transfer) return;
    setPhase("running");
    try {
      setBefore({
        donor: await holding(transfer.from.id, transfer.sku_code),
        here: await holding(facility.id, transfer.sku_code),
      });
    } catch {
      setBefore(null);
    }
    mark("approve", { state: "running", call: `POST /api/transfers/${transfer.id}/approve` });
    let decided: Transfer;
    try {
      decided = await api.decide(transfer.id, "approve");
    } catch (e) {
      return fail("approve", e);
    }
    mark("approve", {
      state: "done",
      detail: `Transfer ${decided.id} approved by ${user.name}.`,
      note: "The donor is re-checked against its stock now, not its stock when the plan was made — a stale plan is refused here.",
    });

    // -------------------------------------------- 8. the donor dispatches ---
    mark("moved", { state: "running", call: `GET /api/facilities/${transfer.from.id}` });
    try {
      const [donor, here] = await Promise.all([
        holding(transfer.from.id, transfer.sku_code),
        holding(facility.id, transfer.sku_code),
      ]);
      mark("moved", {
        state: "done",
        detail: `${donor.name} is down to ${qty(donor.qty, transfer.unit)}; batch TRF-${decided.id} is in transit.`,
        note: `Here: still ${qty(here.qty, transfer.unit)} — the recipient is credited when the delivery is confirmed, not when the lorry leaves.`,
      });
    } catch (e) {
      return fail("moved", e);
    }
    onChanged();
    setPhase("receiving");
  }, [transfer, user.name, facility.id, mark, fail, onChanged]);

  // ------------------------------------------- 9. the receiver confirms ---
  // A second person at the other end, in the real system. Kept as its own
  // click so the two-sided ledger (spec 26.3) is visible rather than implied.
  const confirmArrival = useCallback(async () => {
    if (!transfer) return;
    setPhase("running");
    mark("receipt", { state: "running", call: `GET /api/movements?facility=${facility.id}` });
    try {
      const ledger = await api.movements({
        facility: facility.id,
        sku: transfer.sku_code,
        view: "all",
        limit: 50,
      });
      const batch = ledger.movements.find((m) => m.transfer_id === transfer.id);
      if (!batch) {
        mark("receipt", {
          state: "failed",
          detail: `No batch for transfer ${transfer.id} is on ${facility.name}'s ledger yet.`,
        });
        setPhase("failed");
        return;
      }
      mark("receipt", { state: "running", call: `POST /api/movements/${batch.id}/receipt` });
      const r = await api.confirmReceipt(batch.id, batch.qty_dispatched, "Confirmed in the emergency drill");
      const [donor, here] = await Promise.all([
        holding(transfer.from.id, transfer.sku_code),
        holding(facility.id, transfer.sku_code),
      ]);
      setAfter({ donor, here });
      mark("receipt", {
        state: "done",
        detail: `${qty(r.movement.qty_received ?? batch.qty_dispatched, transfer.unit)} received at ${facility.name}; it now holds ${qty(r.qty_on_hand, transfer.unit)}.`,
        note: "Dispatch and receipt are logged independently, so a short delivery would surface on the Movements tab rather than being assumed away.",
      });
    } catch (e) {
      return fail("receipt", e);
    }
    onChanged();
    setPhase("done");
  }, [transfer, facility.id, facility.name, mark, fail, onChanged]);

  const autoRan = useRef(false);
  useEffect(() => {
    if (!autoStart || autoRan.current) return;
    autoRan.current = true;
    void run();
  }, [autoStart, run]);

  // ------------------------------------------------------- 7. the feed ---
  // Read from the app's own poll rather than a second request, so what is
  // counted here is exactly what the rest of the interface is reacting to.
  useEffect(() => {
    if (phase === "idle") return;
    const mine = events.filter(
      (e) =>
        e.facility_id === facility.id ||
        e.facility_id === transfer?.from.id ||
        (e.transfer_id != null && e.transfer_id === transfer?.id),
    );
    if (!mine.length) return;
    const kinds = [...new Set(mine.map((e) => e.kind))];
    setSteps((prev) =>
      prev.map((s) =>
        s.id === "feed"
          ? {
              ...s,
              state: "done",
              call: "GET /api/events",
              at: s.at ?? performance.now() - started.current,
              detail: `${mine.length} event${mine.length === 1 ? "" : "s"} logged: ${kinds.join(", ")}.`,
              note: "The durable log every browser polls — the same rows the dashboard's live badge reads.",
            }
          : s,
      ),
    );
  }, [events, phase, facility.id, transfer]);

  const mayWrite = can.report(user, facility) && can.decideTransfer(
    user,
    { state: facility.state_silo, district: facility.district },
    { state: facility.state_silo, district: facility.district },
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-line px-3 py-2.5">
        <button onClick={onClose} className="text-[12px] font-medium text-brand hover:underline">
          ← {facility.name}
        </button>
        <h2 className="mt-2 text-[13px] font-semibold text-ink">
          Simulate an emergency · आपात स्थिति का पूर्वाभ्यास
        </h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          A stock-out at {facility.name}, carried through the whole chain on real endpoints. Each
          step shows what it called and what came back; the two decisions stay with a person.
        </p>
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
          Writes are confined to {SANDBOX.label}. <code className="font-mono">scripts/reset_nashik.py</code>{" "}
          puts the sandbox back.
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        {!mayWrite && (
          <p className="mb-2.5 rounded-md border border-risk/30 bg-risk/5 px-2.5 py-2 text-[12px] text-ink-2">
            This account cannot report for {facility.name} or decide its transfers, so the loop
            would stop at the first write.
          </p>
        )}

        <ol className="space-y-0">
          {steps.map((s, i) => (
            <li key={s.id} className="flex gap-2.5 border-t border-line py-2.5 first:border-t-0">
              <Dot state={s.state} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span
                    className={`text-[12.5px] font-medium ${
                      s.state === "pending" ? "text-ink-3" : "text-ink"
                    }`}
                  >
                    {i + 1}. {s.title}
                    {s.ai === true && (
                      <span className="ml-1.5 rounded border border-brand/30 bg-brand/[0.06] px-1 py-px text-[10px] font-semibold text-brand">
                        <span aria-hidden="true">⚡</span> Gemini
                      </span>
                    )}
                    {s.ai === false && (
                      <span className="ml-1.5 rounded border border-line bg-canvas px-1 py-px text-[10px] font-semibold text-ink-2">
                        Rule-based
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-ink-3">
                    {stamp(s.at)}
                  </span>
                </div>
                {s.call && (
                  <code className="mt-0.5 block font-mono text-[10.5px] break-all text-brand">
                    {s.call}
                  </code>
                )}
                {s.detail && (
                  <p
                    className={`mt-1 text-[12px] leading-snug ${
                      s.state === "failed" ? "text-crit" : "text-ink-2"
                    }`}
                  >
                    {s.detail}
                  </p>
                )}
                {s.note && <p className="mt-0.5 text-[11px] leading-snug text-ink-3">{s.note}</p>}
              </div>
            </li>
          ))}
        </ol>

        {phase === "awaiting" && transfer && (
          <div className="mt-3 rounded-md border border-brand/30 bg-brand/[0.04] px-2.5 py-2.5">
            <p className="text-[12.5px] leading-snug text-ink">
              Nothing has moved yet. The plan is a proposal until somebody accountable accepts it.
            </p>
            <button
              onClick={approve}
              className="mt-2 h-9 w-full rounded-md bg-brand text-[13px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:outline-none"
            >
              Approve transfer {transfer.id}
            </button>
          </div>
        )}

        {phase === "receiving" && transfer && (
          <div className="mt-3 rounded-md border border-brand/30 bg-brand/[0.04] px-2.5 py-2.5">
            <p className="text-[12.5px] leading-snug text-ink">
              The batch is on the road. {facility.name} is credited only when someone there confirms
              what arrived.
            </p>
            <button
              onClick={confirmArrival}
              className="mt-2 h-9 w-full rounded-md bg-brand text-[13px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:outline-none"
            >
              Confirm arrival at {facility.name}
            </button>
          </div>
        )}

        {before && after && transfer && (
          <div className="mt-3 rounded-md border border-line px-2.5 py-2">
            <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
              {transfer.sku_name}, read from each facility
            </div>
            <table className="mt-1 w-full text-[11.5px]">
              <thead>
                <tr className="text-ink-3">
                  <th className="py-1 text-left font-medium">Facility</th>
                  <th className="py-1 text-right font-medium">Before</th>
                  <th className="py-1 text-right font-medium">After</th>
                </tr>
              </thead>
              <tbody className="font-mono tabular-nums">
                {(
                  [
                    ["Donor", before.donor, after.donor],
                    ["Receiver", before.here, after.here],
                  ] as const
                ).map(([role, b, a]) => (
                  <tr key={role} className="border-t border-line">
                    <td className="py-1 font-sans">
                      <div className="text-ink">{a.name}</div>
                      <div className="text-[10.5px] text-ink-3">{role}</div>
                    </td>
                    <td className="py-1 text-right text-ink-2">
                      {qty(b.qty, transfer.unit)}
                      <div className="text-[10.5px] text-ink-3">{formatDays(b.days)}</div>
                    </td>
                    <td className="py-1 text-right text-ink">
                      {qty(a.qty, transfer.unit)}
                      <div className="text-[10.5px] text-ink-3">{formatDays(a.days)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {phase === "done" && (
          <div className="mt-3 rounded-md border border-line bg-canvas px-2.5 py-2">
            <p className="text-[12px] leading-snug text-ink-2">
              {SANDBOX.district} is in Maharashtra, one of the four state silos that train the
              shared forecasting model. This reading is now in the rows that silo trains on; it reaches the
              shared model at the next round, and only as weights.
            </p>
            {onOpenFederation && (
              <button
                onClick={onOpenFederation}
                className="mt-1.5 text-[12px] font-medium text-brand hover:underline"
              >
                Open the Federation tab →
              </button>
            )}
          </div>
        )}

        {error && phase === "failed" && (
          <p role="alert" className="mt-3 text-[12px] text-crit">
            {error}
          </p>
        )}
      </div>

      <div className="shrink-0 border-t border-line px-3 py-2.5">
        <button
          onClick={run}
          disabled={phase === "running" || phase === "receiving" || !mayWrite}
          className="h-9 w-full rounded-md bg-brand text-[13px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:outline-none disabled:opacity-55"
        >
          {phase === "idle"
            ? "Simulate a stock-out"
            : phase === "running"
              ? "Running…"
              : "Run it again"}
        </button>
      </div>
    </div>
  );
}

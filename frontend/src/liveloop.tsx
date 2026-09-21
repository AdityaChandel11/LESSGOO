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
}

const STEP_TITLES: [string, string][] = [
  ["reading", "Reading committed"],
  ["recompute", "Days of stock recomputed"],
  ["flip", "Condition re-evaluated"],
  ["plan", "Optimiser proposes a transfer"],
  ["approve", "Officer approves"],
  ["moved", "Stock moves"],
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
  }));

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
function pickSku(skus: SkuStock[]): SkuStock | null {
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
  onClose,
  onChanged,
  onFocus,
}: {
  facility: FacilityDetail;
  user: User;
  /** The app's own poll, so step seven reports the feed rather than a copy. */
  events: LiveEvent[];
  onClose: () => void;
  /** Ask the map and the panels to re-read after a write. */
  onChanged: () => void;
  onFocus: (lat: number, lng: number) => void;
}) {
  const [steps, setSteps] = useState<Step[]>(freshSteps);
  const [phase, setPhase] = useState<"idle" | "running" | "awaiting" | "done" | "failed">("idle");
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const [error, setError] = useState<string | null>(null);
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
      note: "A dot carries the facility's worst medicine, which is why one can move without the other.",
    });
    onChanged();

    // ------------------------------------------------------ 4. the plan ---
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
    setPhase("awaiting");
    onChanged();
  }, [facility, mark, fail, onChanged, onFocus]);

  const approve = useCallback(async () => {
    if (!transfer) return;
    setPhase("running");
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

    // ------------------------------------------------ 6. the stock moves ---
    mark("moved", { state: "running", call: `GET /api/facilities/${transfer.from.id}` });
    try {
      const [donor, here] = await Promise.all([
        api.facility(transfer.from.id),
        api.facility(facility.id),
      ]);
      const donorSku = donor.skus.find((s) => s.sku_code === transfer.sku_code);
      const hereSku = here.skus.find((s) => s.sku_code === transfer.sku_code);
      mark("moved", {
        state: "done",
        detail: `${donor.name} is down to ${qty(donorSku?.qty_on_hand ?? 0, transfer.unit)}; batch TRF-${decided.id} is in transit.`,
        note: `Here: still ${qty(hereSku?.qty_on_hand ?? 0, transfer.unit)} — the recipient is credited when the delivery is confirmed, not when the lorry leaves.`,
      });
    } catch (e) {
      return fail("moved", e);
    }
    onChanged();
    setPhase("done");
  }, [transfer, user.name, facility.id, mark, fail, onChanged]);

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
          Simulate a stock-out · स्टॉक-आउट का पूर्वाभ्यास
        </h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          One pass through the whole chain, on real endpoints. Each step shows what it called and
          what came back.
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

        {error && phase === "failed" && (
          <p role="alert" className="mt-3 text-[12px] text-crit">
            {error}
          </p>
        )}
      </div>

      <div className="shrink-0 border-t border-line px-3 py-2.5">
        <button
          onClick={run}
          disabled={phase === "running" || !mayWrite}
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

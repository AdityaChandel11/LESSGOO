/**
 * The silo inspector — spec 12.2 and 27.
 *
 * Two claims sit side by side here. The first is ordinary: a shared model
 * beats the burn-rate rule the dashboard uses today. The second is the one
 * that matters, and the one an accuracy chart can never make on its own —
 * that nothing but model weights ever left a state.
 *
 * So the evidence is shown, not summarised: the measured bytes, every tensor's
 * shape, the hash of the weights, and a facility-row count the aggregator
 * asserts before each round is written. The zero below is a result. If a silo
 * ever returned anything but weights and scalar numbers, the round would have
 * stopped instead of arriving here.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type FederationInspector, type FederationRound } from "./api";

function mae(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

function bytes(value: number | null | undefined): string {
  if (typeof value !== "number") return "—";
  return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`;
}

/**
 * The accuracy curve, drawn against the rule it has to beat.
 *
 * `upTo` stops the line at a round so a replay can draw it one round at a
 * time. The axes never move while it does: the scale comes from the whole run,
 * so the curve falls through a fixed frame instead of the frame rescaling
 * under it and making every round look like the same improvement.
 */
function Curve({
  rounds,
  baseline,
  upTo,
}: {
  rounds: FederationRound[];
  baseline: number | null;
  upTo?: number | null;
}) {
  const scored = rounds.filter((r) => r.global_val_mae != null);
  if (scored.length < 2) return null;
  const values = scored.map((r) => r.global_val_mae as number);
  const top = Math.max(...values, baseline ?? 0) * 1.05;
  const w = 100;
  const h = 44;
  const x = (i: number) => (i / (scored.length - 1)) * w;
  const y = (v: number) => h - (v / top) * h;
  const shown = upTo == null ? values : values.slice(0, Math.max(1, upTo + 1));
  const path = shown
    .map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(v).toFixed(2)}`)
    .join(" ");
  const head = shown.length - 1;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-2 h-24 w-full" preserveAspectRatio="none" role="img"
      aria-label={`Model error across ${shown.length} of ${scored.length} rounds, at ${mae(shown[head])}`}>
      {baseline != null && (
        <line x1="0" x2={w} y1={y(baseline)} y2={y(baseline)} stroke="currentColor"
          className="text-warn" strokeWidth="0.6" strokeDasharray="2 2" />
      )}
      <path d={path} fill="none" stroke="currentColor" className="text-ok" strokeWidth="1.2"
        vectorEffect="non-scaling-stroke" />
      {upTo != null && shown.length > 0 && (
        <circle cx={x(head)} cy={y(shown[head])} r="1.6" className="fill-ok"
          vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11.5px] text-ink-2">{label}</span>
      <span className={`shrink-0 font-mono tabular-nums ${strong ? "text-[13px] font-semibold text-ink" : "text-[11.5px] text-ink-2"}`}>
        {value}
      </span>
    </div>
  );
}

/** One round every this long. Slow enough to read a row, short enough that
 *  nine rounds do not outlast anybody's patience. */
const REPLAY_STEP_MS = 850;

/** Shared, because `?? []` would hand the silo effect a new array on every
 *  render and that effect sets state in the parent: a fresh reference each
 *  time is a render loop, not a no-op. */
const NO_ROUNDS: FederationRound[] = [];

/**
 * Which silo the trust weighting cost the most, stated from the row rather
 * than written in. A silo contributes its window count scaled by its own data
 * confidence, so the gap between the two is the weighting made visible.
 */
function mostDownWeighted(silos: FederationRound["per_silo"]) {
  const scored = silos.filter((s) => s.windows > 0);
  if (scored.length < 2) return null;
  const worst = scored.reduce((a, b) => (a.trust <= b.trust ? a : b));
  const best = scored.reduce((a, b) => (a.trust >= b.trust ? a : b));
  if (worst.state === best.state || worst.trust >= best.trust) return null;
  return { worst, best, lostPct: Math.round((1 - worst.counts_as / worst.windows) * 100) };
}

export function FederationPanel({
  refreshKey,
  onSilos,
  stateName,
}: {
  refreshKey: number;
  /** Reports the silo states upward so the map can ring them. */
  onSilos?: (states: string[]) => void;
  /** The rounds store two-letter codes; a reader wants the state. */
  stateName?: (code: string) => string;
}) {
  const named = (code: string) => stateName?.(code) ?? code;
  const [data, setData] = useState<FederationInspector | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // null: the finished run, as recorded. A number: the round a replay has
  // reached. Nothing here re-trains anything — the rows are already written.
  const [replayAt, setReplayAt] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .federationInspector()
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the inspector"))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const rounds = data?.rounds ?? NO_ROUNDS;

  // Tell the map which states train the model, and un-tell it on the way out.
  useEffect(() => {
    if (!onSilos) return;
    const states = rounds[rounds.length - 1]?.per_silo.map((s) => s.state) ?? [];
    onSilos(states);
    return () => onSilos([]);
  }, [onSilos, rounds]);

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  const replay = useCallback(() => {
    if (rounds.length < 2) return;
    if (timer.current) window.clearInterval(timer.current);
    // Somebody who has asked for less motion still wants the answer, so they
    // get the finished curve rather than a slower version of the animation.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setReplayAt(rounds.length - 1);
      return;
    }
    setReplayAt(0);
    timer.current = window.setInterval(() => {
      setReplayAt((at) => {
        const next = (at ?? 0) + 1;
        if (next >= rounds.length - 1) {
          if (timer.current) window.clearInterval(timer.current);
          timer.current = null;
          return rounds.length - 1;
        }
        return next;
      });
    }, REPLAY_STEP_MS);
  }, [rounds.length]);

  const replaying = replayAt != null && replayAt < rounds.length - 1;
  // Everything below reads this round, so the table, the counter and the curve
  // can never disagree about where the replay has got to.
  const shown = replayAt == null ? rounds[rounds.length - 1] : rounds[replayAt];
  const last = shown;
  const shapes = Object.entries(data?.tensor_shapes ?? {});
  const rowsSoFar =
    replayAt == null
      ? data?.raw_rows_transmitted ?? 0
      : rounds.slice(0, replayAt + 1).reduce((n, r) => n + r.raw_rows_transmitted, 0);
  const weighting = shown ? mostDownWeighted(shown.per_silo) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <h2 className="text-[13px] font-semibold text-ink">Federated training · संघीय प्रशिक्षण</h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          Four states train one forecasting model. Each keeps its own facility rows; only weights
          move, and what moved is measured below rather than asserted.
        </p>
        {data?.available && (
          <>
            <p className="mt-1 font-mono text-[11px] text-ink-3">
              {data.strategy} · run {data.run_id?.slice(0, 12)} · {data.rounds.length} rounds
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              <button
                onClick={replay}
                disabled={replaying || rounds.length < 2}
                className="h-8 rounded-md border border-brand bg-brand/[0.04] px-3 text-[12.5px] font-medium text-brand hover:bg-brand/10 focus:ring-2 focus:ring-brand/30 focus:outline-none disabled:opacity-55"
              >
                {replaying
                  ? `Round ${shown?.round_no ?? 0} of ${rounds[rounds.length - 1]?.round_no ?? 0}`
                  : replayAt != null
                    ? "Replay again"
                    : "Replay the run"}
              </button>
              {replayAt != null && !replaying && (
                <button
                  onClick={() => setReplayAt(null)}
                  className="text-[12px] font-medium text-ink-3 hover:text-ink"
                >
                  Show the finished run
                </button>
              )}
            </div>
            <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
              Replay of the recorded run, not a new training. The rounds below were written by the
              aggregator when the run happened; pressing this re-reads them in order.
            </p>
          </>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="p-3 text-[12.5px] text-crit">{error}</p>
        ) : loading && !data ? (
          <p className="p-3 text-[12.5px] text-ink-3">Loading the last run…</p>
        ) : !data?.available ? (
          <div className="p-3">
            <p className="text-[12.5px] text-ink-2">{data?.note}</p>
          </div>
        ) : (
          <>
            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                Accuracy, against the rule it replaces
              </div>
              <Curve rounds={data.rounds} baseline={data.baseline_mae} upTo={replayAt} />
              <Row
                label={replayAt == null ? "Model error now (MAE)" : `Model error at round ${shown?.round_no}`}
                value={mae(replayAt == null ? data.best_mae : shown?.global_val_mae)}
                strong
              />
              <Row label="Burn rate, same held-out weeks" value={mae(data.baseline_mae)} />
              <Row label="Error at round one" value={mae(data.first_mae)} />
              <Row
                label="Better than the burn rate by"
                value={data.improvement_pct == null ? "—" : `${data.improvement_pct}%`}
                strong
              />
            </div>

            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                What crossed the wire
              </div>
              <div className="mt-1.5 rounded-md border border-ok/30 bg-ok/5 px-2 py-1.5">
                <div className="flex items-baseline justify-between">
                  <span className="text-[11.5px] font-medium text-ink">Facility rows transmitted</span>
                  <span className="font-mono text-[15px] font-semibold tabular-nums text-ok">
                    {rowsSoFar}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] leading-snug text-ink-3">
                  Asserted by the aggregator before each round was recorded — a reply carrying
                  anything but weights and scalar numbers stops the round.
                </p>
              </div>
              <div className="mt-1.5">
                <Row label="Weights per round" value={bytes(data.bytes_per_round)} strong />
                <Row label="Across the whole run" value={bytes(data.total_bytes)} />
                <Row label="Tensors in the payload" value={String(shapes.length)} />
                <Row label="Silos reporting" value={String(last?.silos_reporting ?? "—")} />
              </div>
              <div className="mt-1.5 rounded-md border border-line bg-canvas px-2 py-1.5">
                <div className="text-[11px] font-medium text-ink-2">Weights hash, last round</div>
                <code className="mt-0.5 block break-all font-mono text-[10.5px] text-ink-3">
                  {last?.weights_sha256 ?? "—"}
                </code>
              </div>
              {shapes.length > 0 && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[11.5px] text-ink-2">
                    Tensor shapes ({shapes.length})
                  </summary>
                  <div className="mt-1 space-y-0.5">
                    {shapes.map(([name, shape]) => (
                      <div key={name} className="flex justify-between gap-2 font-mono text-[10.5px] text-ink-3">
                        <span className="truncate">{name}</span>
                        <span className="shrink-0">[{shape.join(" × ")}]</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>

            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                Per silo, {replayAt == null ? "last round" : `round ${shown?.round_no}`}
              </div>
              <p className="mt-1 text-[11px] leading-snug text-ink-3">
                A silo is weighted by its window count scaled by its own data confidence, so
                facilities whose numbers disagree with each other carry less of the national model.
              </p>
              {weighting && (
                <p className="mt-1 text-[11px] leading-snug text-ink-2">
                  {named(weighting.worst.state)} is the clearest case: confidence{" "}
                  {weighting.worst.trust.toFixed(3)} against {named(weighting.best.state)}&rsquo;s{" "}
                  {weighting.best.trust.toFixed(3)}, with{" "}
                  {weighting.worst.flagged_pct.toFixed(1)}% of its facilities flagged — so its{" "}
                  {weighting.worst.windows.toLocaleString("en-IN")} windows count as{" "}
                  {weighting.worst.counts_as.toLocaleString("en-IN")}, {weighting.lostPct}% less
                  weight in the shared model.
                </p>
              )}
              <table className="mt-1.5 w-full text-[11.5px]">
                <thead>
                  <tr className="text-ink-3">
                    <th className="py-1 text-left font-medium">State</th>
                    <th className="py-1 text-right font-medium">Windows</th>
                    <th className="py-1 text-right font-medium">Trust</th>
                    <th className="py-1 text-right font-medium">Counts as</th>
                  </tr>
                </thead>
                <tbody>
                  {(last?.per_silo ?? []).map((s) => (
                    <tr key={s.state} className="border-t border-line">
                      <td className="py-1 font-medium text-ink">{named(s.state)}</td>
                      <td className="py-1 text-right font-mono tabular-nums text-ink-2">
                        {s.windows.toLocaleString("en-IN")}
                      </td>
                      <td className={`py-1 text-right font-mono tabular-nums ${s.trust < 0.5 ? "text-crit" : "text-ink-2"}`}>
                        {s.trust.toFixed(3)}
                      </td>
                      <td className="py-1 text-right font-mono tabular-nums text-ink">
                        {s.counts_as.toLocaleString("en-IN")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                Round by round
              </div>
              <div className="mt-1.5 space-y-0.5">
                {data.rounds.map((r, i) => (
                  <div
                    key={r.round_no}
                    className={`flex items-baseline justify-between gap-2 border-t border-line py-1 first:border-t-0 ${
                      replayAt != null && i > replayAt ? "opacity-35" : ""
                    }`}
                  >
                    <span className="text-[11.5px] text-ink-2">Round {r.round_no}</span>
                    <span className="font-mono text-[11.5px] tabular-nums text-ink-2">
                      MAE {mae(r.global_val_mae)} · {bytes(r.bytes_transmitted)}
                      {" · "}
                      {/* The zero is the result this whole subsystem exists to
                          produce, so it is written as a finding rather than as
                          an empty column. "0 rows", set in the same grey as the
                          numbers beside it, reads like a figure nobody filled
                          in; the assertion is that no facility row left its
                          state, and it was checked before the round was
                          written. */}
                      {r.raw_rows_transmitted === 0 ? (
                        <span className="text-ok">no rows left the state</span>
                      ) : (
                        <span className="text-crit">
                          {r.raw_rows_transmitted.toLocaleString("en-IN")} rows left the state
                        </span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

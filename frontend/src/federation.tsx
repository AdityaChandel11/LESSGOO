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

import { useEffect, useState } from "react";
import { ApiError, api, type FederationInspector, type FederationRound } from "./api";

function mae(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

function bytes(value: number | null | undefined): string {
  if (typeof value !== "number") return "—";
  return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`;
}

/** The accuracy curve, drawn against the rule it has to beat. */
function Curve({ rounds, baseline }: { rounds: FederationRound[]; baseline: number | null }) {
  const scored = rounds.filter((r) => r.global_val_mae != null);
  if (scored.length < 2) return null;
  const values = scored.map((r) => r.global_val_mae as number);
  const top = Math.max(...values, baseline ?? 0) * 1.05;
  const w = 100;
  const h = 44;
  const x = (i: number) => (i / (scored.length - 1)) * w;
  const y = (v: number) => h - (v / top) * h;
  const path = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-2 h-24 w-full" preserveAspectRatio="none" role="img"
      aria-label={`Model error across ${scored.length} rounds, ending at ${mae(values[values.length - 1])}`}>
      {baseline != null && (
        <line x1="0" x2={w} y1={y(baseline)} y2={y(baseline)} stroke="currentColor"
          className="text-warn" strokeWidth="0.6" strokeDasharray="2 2" />
      )}
      <path d={path} fill="none" stroke="currentColor" className="text-ok" strokeWidth="1.2"
        vectorEffect="non-scaling-stroke" />
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

export function FederationPanel({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<FederationInspector | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .federationInspector()
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the inspector"))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const last = data?.rounds?.[data.rounds.length - 1];
  const shapes = Object.entries(data?.tensor_shapes ?? {});

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <h2 className="text-[13px] font-semibold text-ink">Federated training · संघीय प्रशिक्षण</h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          Four states train one forecasting model. Each keeps its own facility rows; only weights
          move, and what moved is measured below rather than asserted.
        </p>
        {data?.available && (
          <p className="mt-1 font-mono text-[11px] text-ink-3">
            {data.strategy} · run {data.run_id?.slice(0, 12)} · {data.rounds.length} rounds
          </p>
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
              <Curve rounds={data.rounds} baseline={data.baseline_mae} />
              <Row label="Model error now (MAE)" value={mae(data.best_mae)} strong />
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
                    {data.raw_rows_transmitted}
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
                Per silo, last round
              </div>
              <p className="mt-1 text-[11px] leading-snug text-ink-3">
                A silo is weighted by its window count scaled by its own data confidence, so
                facilities whose numbers disagree with each other carry less of the national model.
              </p>
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
                      <td className="py-1 font-medium text-ink">{s.state}</td>
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
                {data.rounds.map((r) => (
                  <div key={r.round_no} className="flex items-baseline justify-between gap-2 border-t border-line py-1 first:border-t-0">
                    <span className="text-[11.5px] text-ink-2">Round {r.round_no}</span>
                    <span className="font-mono text-[11.5px] tabular-nums text-ink-2">
                      MAE {mae(r.global_val_mae)} · {bytes(r.bytes_transmitted)} · {r.raw_rows_transmitted} rows
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

/**
 * The proof layer — spec Section 19.
 *
 * Every claim this platform makes, as a number with the date it was computed
 * and the seed it came from. Nothing here is typed by hand; it is the last
 * recorded run of `python -m scripts.run_eval`, so the screen and the spoken
 * pitch cannot drift apart.
 *
 * The caveats are shown, not buried. A computed figure with its limits stated
 * survives a hostile question; a confident claim does not, and this panel
 * exists precisely for the judge who asks "how do you know?".
 */

import { useEffect, useState } from "react";
import { ApiError, api, type EvalReport } from "./api";

/** Percentages read to one decimal; three makes a rate look like a measurement
 *  it is not. */
function pct(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(1)}%` : "—";
}

function num(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString("en-IN") : value.toFixed(3);
  }
  return String(value);
}

function Row({ label, value, strong }: { label: string; value: unknown; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11.5px] text-ink-2">{label}</span>
      <span
        className={`shrink-0 font-mono tabular-nums ${
          strong ? "text-[13px] font-semibold text-ink" : "text-[11.5px] text-ink-2"
        }`}
      >
        {num(value)}
      </span>
    </div>
  );
}

function Section({
  title,
  body,
  children,
}: {
  title: string;
  body: Record<string, unknown> | undefined;
  children?: React.ReactNode;
}) {
  if (!body) return null;
  if (body.available === false) {
    return (
      <div className="border-b border-line px-3 py-2.5">
        <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
          {title}
        </div>
        <p className="mt-1 text-[11.5px] text-ink-3">Not measured yet — {String(body.reason)}.</p>
      </div>
    );
  }
  return (
    <div className="border-b border-line px-3 py-2.5">
      <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
        {title}
      </div>
      <div className="mt-1.5">{children}</div>
      {typeof body.note === "string" && (
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">{body.note}</p>
      )}
    </div>
  );
}

export function ProofPanel({ refreshKey }: { refreshKey: number }) {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .evalReport()
      .then(setReport)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the report"))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const s = report?.sections ?? {};
  const trust = s.trust as Record<string, unknown> | undefined;
  const redistribution = s.redistribution as Record<string, unknown> | undefined;
  const federation = s.federation as Record<string, unknown> | undefined;
  const impact = s.impact as Record<string, unknown> | undefined;
  const baselines = (federation?.baselines ?? {}) as Record<string, number | null>;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <h2 className="text-[13px] font-semibold text-ink">What can be proved</h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          Every number below was computed by the eval harness, not written into a slide. All of it
          is a simulation over synthetic data with a recorded seed — which is said here rather
          than discovered later.
        </p>
        {report && (
          <p className="mt-1 font-mono text-[11px] text-ink-3">
            run {new Date(report.run_at).toLocaleString("en-IN")} · seed {report.dataset_seed ?? "—"}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="p-3 text-[12.5px] text-crit">{error}</p>
        ) : loading && !report ? (
          <p className="p-3 text-[12.5px] text-ink-3">Loading the last run…</p>
        ) : !report ? (
          <div className="p-3">
            <p className="text-[12.5px] text-ink-2">No eval run has been recorded.</p>
            <p className="mt-1.5 font-mono text-[11.5px] text-ink-3">
              cd backend && python -m scripts.run_eval
            </p>
          </div>
        ) : (
          <>
            <Section title="Stock-outs, counted from the history" body={impact}>
              <Row label="Facility-days with nothing on the shelf" value={impact?.facility_days_out_of_stock} strong />
              <Row label="Medicine existed within 150 km that day" value={pct(impact?.stock_within_reach_pct)} strong />
              <Row label="Facilities affected" value={impact?.facilities_affected} />
              <Row label="Nothing reachable at all" value={impact?.no_reachable_stock} />
            </Section>

            <Section title="Trust layer, against the generator's ground truth" body={trust}>
              <Row label="Dishonest facilities it caught (recall)" value={trust?.recall} strong />
              <div className="mt-1 rounded-md border border-line bg-canvas px-2 py-1.5">
                <div className="text-[11px] font-medium text-ink-2">
                  Of the worst-ranked facilities, how many are genuinely bad
                </div>
                {Object.entries((trust?.precision_at_k ?? {}) as Record<string, number>).map(
                  ([k, v]) => (
                    <Row key={k} label={`Top ${k.replace("top_", "")} on the queue`} value={v} />
                  ),
                )}
              </div>
              <Row label="Precision across every flag" value={trust?.precision} />
              <Row label="False-positive rate" value={trust?.false_positive_rate} />
              <Row label="Deliberately dishonest facilities" value={trust?.deliberately_dishonest} />
              <Row label="Flagged by the layer" value={trust?.flagged} />
            </Section>

            <Section title="Redistribution, against its own safety rules" body={redistribution}>
              <Row label="Constraint violations" value={redistribution?.constraint_violations} strong />
              <Row label="Transfers proposed" value={redistribution?.proposals} />
              <Row label="Units moved" value={redistribution?.units_moved} />
              <ul className="mt-1 space-y-0.5">
                {((redistribution?.rules_checked ?? []) as string[]).map((r) => (
                  <li key={r} className="flex gap-1.5 text-[11px] text-ink-3">
                    <span className="text-ok">✓</span>
                    {r}
                  </li>
                ))}
              </ul>
            </Section>

            <Section title="Federated forecasting, as measured mid-run" body={federation}>
              <Row label="Model error (MAE)" value={federation?.model_mae} strong />
              <Row label="Better than the burn rate by" value={pct(federation?.improvement_vs_burn_rate_pct)} strong />
              <div className="mt-1 rounded-md border border-line bg-canvas px-2 py-1.5">
                <div className="text-[11px] font-medium text-ink-2">Naive rules it had to beat</div>
                <Row label="Last four weeks' mean" value={baselines["28_day_mean"]} />
                <Row label="Yesterday's consumption" value={baselines.last_value} />
                <Row label="Same day last week" value={baselines.same_day_last_week} />
              </div>
              <Row label="Facility rows transmitted" value={federation?.raw_rows_transmitted} />
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

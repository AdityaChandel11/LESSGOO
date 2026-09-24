import { useEffect, useState } from "react";
import { type Outbreaks, type StockingAdvice, api } from "./api";

const SHOWN = 3;

function day(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })
    : "—";
}

/**
 * Outbreaks from the IDSP Weekly Outbreak Report (NCDC). The rows were parsed
 * once from the published PDFs; the report weeks are printed so a reader never
 * takes an old report for this week's.
 */
export function OutbreakWarnings({ state, stateLabel }: { state: string | null; stateLabel: string }) {
  const [data, setData] = useState<Outbreaks | null>(null);
  const [open, setOpen] = useState(false);
  const [advice, setAdvice] = useState<StockingAdvice[] | null>(null);
  const [showAdvice, setShowAdvice] = useState(false);

  useEffect(() => {
    api.outbreaks(state).then(setData).catch(() => setData(null));
  }, [state]);

  useEffect(() => {
    if (!showAdvice) return;
    let alive = true;
    setAdvice(null);
    api
      .outbreakAdvice(state)
      .then((a) => alive && setAdvice(a))
      .catch(() => alive && setAdvice([]));
    return () => {
      alive = false;
    };
  }, [state, showAdvice]);

  const toggleAdvice = () => setShowAdvice((s) => !s);

  if (!data) return null;
  const rows = open ? data.rows : data.rows.slice(0, SHOWN);
  const weeks = data.reports.map((r) => `week ${r.week}/${r.year}`).join(", ");

  return (
    <section className="shrink-0 border-b border-line bg-panel px-3 py-2.5" aria-label="Outbreak warnings">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[12.5px] font-semibold text-ink">
          Outbreak warnings · प्रकोप चेतावनी{" "}
          <span className="font-mono text-[11px] font-normal text-ink-3">{data.rows.length}</span>
        </h2>
        <span className="text-[10.5px] text-ink-3">{stateLabel}</span>
      </div>
      <p className="mt-0.5 text-[10.5px] text-ink-3">
        Source:{" "}
        <a href={data.source_url} target="_blank" rel="noreferrer" className="underline hover:text-brand">
          {data.source}
        </a>{" "}
        · {weeks}
      </p>
      {data.rows.length === 0 ? (
        <p className="mt-1.5 text-[11.5px] text-ink-2">No outbreak in these reports for {stateLabel}.</p>
      ) : (
        <table className="mt-1.5 w-full text-[11px]">
          <thead>
            <tr className="text-left text-ink-3">
              <th className="py-0.5 font-medium">{data.columns[0]}</th>
              <th className="py-0.5 font-medium">District</th>
              <th className="py-0.5 text-right font-medium">Cases</th>
              <th className="py-0.5 text-right font-medium">Deaths</th>
              <th className="py-0.5 pl-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.unique_id} className="border-t border-line align-top" title={`${r.unique_id} · ${data.columns[3]}: ${day(r.start_date)}`}>
                <td className="py-1 font-medium text-ink">{r.disease}</td>
                <td className="py-1 text-ink-2">
                  {r.district}
                  {!state && <span className="text-ink-3">, {r.state_code ?? r.state}</span>}
                  {r.in_network && <span className="ml-1 text-[10px] font-medium text-brand">in network</span>}
                </td>
                <td className="py-1 text-right font-mono tabular-nums text-ink">{r.cases}</td>
                <td className={`py-1 text-right font-mono tabular-nums ${r.deaths ? "text-crit" : "text-ink-3"}`}>{r.deaths}</td>
                <td className="py-1 pl-2 text-ink-2">{r.status ?? "not stated"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-1.5 flex gap-3">
        <button onClick={toggleAdvice} className="text-[11.5px] font-medium text-brand hover:underline">
          {showAdvice ? "Hide stocking advice" : "Stocking advice (demo) →"}
        </button>
      </div>
      {showAdvice && (
        <div className="mt-2 rounded-md border border-line bg-canvas px-2.5 py-2">
          <p className="text-[10.5px] leading-snug text-ink-3">
            <span className="font-semibold text-ink-2">How it is built: </span>
            IDSP weekly report → parsed outbreak rows → disease-to-medicine map + monsoon calendar +
            district consumption trend → stocking advice → redistribution plan.{" "}
            <span className="font-medium">Demo: the consumption trend is simulated.</span>
          </p>
          {!advice ? (
            <p className="mt-1.5 text-[11.5px] text-ink-3">Working it out…</p>
          ) : advice.length === 0 ? (
            <p className="mt-1.5 text-[11.5px] text-ink-2">No outbreak here maps to a medicine we stock.</p>
          ) : (
            <ul className="mt-1.5 flex flex-col gap-2">
              {advice.slice(0, 4).map((a) => (
                <li key={a.unique_id} className="rounded border border-line bg-panel px-2 py-1.5">
                  <p className="text-[11.5px] font-medium text-ink">{a.action}</p>
                  <ul className="mt-0.5 list-disc pl-4 text-[10.5px] text-ink-3">
                    {a.signals.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {data.rows.length > SHOWN && (
        <button onClick={() => setOpen((o) => !o)} className="mt-1 text-[11.5px] font-medium text-brand hover:underline">
          {open ? "Show fewer" : `Show all ${data.rows.length}`}
        </button>
      )}
    </section>
  );
}

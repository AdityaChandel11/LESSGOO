import { useEffect, useState } from "react";
import { type Outbreaks, api } from "./api";

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

  useEffect(() => {
    api.outbreaks(state).then(setData).catch(() => setData(null));
  }, [state]);

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
      {data.rows.length > SHOWN && (
        <button onClick={() => setOpen((o) => !o)} className="mt-1 text-[11.5px] font-medium text-brand hover:underline">
          {open ? "Show fewer" : `Show all ${data.rows.length}`}
        </button>
      )}
    </section>
  );
}

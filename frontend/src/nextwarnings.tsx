import { useEffect, useState } from "react";

import { type NextPair, type NextWarnings as Strip, api } from "./api";

/**
 * Fix #45 (absorbs #2): the "Next 14 days" strip. Which district will run
 * short of which medicine, and when — district × medicine pairs whose centres
 * are projected to run out inside the horizon, earliest first.
 *
 * Every date is a centre's last count carried forward at a daily rate, and
 * each row says which rate: the shared model's forecast, the 28-day burn
 * rate, or the outbreak rate. Nothing is listed that the database does not
 * hold; when no pair qualifies the strip says so instead of filling space.
 *
 * `forecastOnly` is the same data as the Federation tab shows it: only the
 * dates that rest on the shared model's fresh forecast.
 */

const SOURCE_WORDS: Record<NextPair["source"], string> = {
  forecast: "shared model's forecast",
  mixed: "forecast and burn rate",
  burn_rate: "28-day burn rate",
  outbreak: "outbreak rate",
};

function day(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function NextWarnings({
  state,
  stateLabel,
  refreshKey,
  forecastOnly = false,
  onOpen,
}: {
  state: string | null;
  stateLabel: string;
  refreshKey: number;
  forecastOnly?: boolean;
  /** Open this pair's recommendations (the Redistribution tab, its state and medicine). */
  onOpen?: (p: NextPair) => void;
}) {
  const [data, setData] = useState<Strip | null>(null);
  const [failed, setFailed] = useState(false);
  // Collapsed to one line by default: the list below it is what the panel is
  // for, and it must keep its height (fix #52).
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .nextWarnings(state, forecastOnly ? "forecast" : "all")
      .then((d) => {
        if (!alive) return;
        setData(d);
        setFailed(false);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [state, forecastOnly, refreshKey]);

  // A server that predates the strip, or one that could not answer: say
  // nothing rather than an error above the panel people came for.
  if (failed || !data) return null;

  const top = data.pairs[0];
  const heading = forecastOnly
    ? "What the shared model is predicting now · साझा मॉडल का पूर्वानुमान"
    : `Next ${data.horizon_days} days · अगले ${data.horizon_days} दिन`;

  return (
    <section aria-label={heading} className="shrink-0 border-b border-line px-4 py-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-baseline justify-between gap-2 text-left"
      >
        <h2 className="text-[12.5px] font-semibold text-ink">
          {open ? "▾" : "▸"} {heading}{" "}
          <span className="font-mono text-[11px] font-normal text-ink-3">{data.pairs.length}</span>
        </h2>
        <span className="text-[10.5px] text-ink-3">{stateLabel}</span>
      </button>

      {!open && top && (
        <p className="mt-0.5 truncate text-[11.5px] text-ink-2">
          First: {top.district} · {top.sku_name} · {day(top.first_on)}
          {top.centres > 1 && ` · ${top.centres} centres`}
        </p>
      )}

      {data.pairs.length === 0 ? (
        <p className="mt-1 text-[11.5px] leading-snug text-ink-2">
          {forecastOnly
            ? `No run-out date rests on the shared model right now: no forecast published in the last ${data.forecast_max_age_days} days is in use, so days of stock come from the 28-day burn rate.`
            : `No district is projected to run out of a medicine in the next ${data.horizon_days} days, from the counts on record.`}
        </p>
      ) : !open ? null : (
        <ul className="mt-1.5 max-h-[40vh] space-y-1.5 overflow-y-auto">
          {data.pairs.map((p) => (
            <li key={`${p.state}:${p.district}:${p.sku_code}`} className="text-[12px] leading-snug">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-medium text-ink">
                  {p.district}
                  {!state && <span className="font-normal text-ink-3">, {p.state_name}</span>} · {p.sku_name}
                </span>
                <span className="shrink-0 font-mono text-[11.5px] tabular-nums text-ink">
                  {day(p.first_on)}
                </span>
              </div>
              <p className="text-[11.5px] text-ink-2">
                {p.centres === 1
                  ? `${p.first_centre} runs out`
                  : `${p.centres} centres run out within ${data.horizon_days} days; first, ${p.first_centre}`}
                {p.outbreak &&
                  ` (${day(p.outbreak.without_on)} without the ${p.outbreak.disease} outbreak)`}
              </p>
              <p className="text-[10.5px] text-ink-3">
                From each centre's last count, at the {SOURCE_WORDS[p.source]}
                {p.source === "mixed" && ` (${p.by_forecast} of ${p.centres} by forecast)`}
                {p.outbreak &&
                  (p.outbreak.basis === "observed"
                    ? " — the district's own rise in use"
                    : " — the declaring officer's assumption")}
                {onOpen && (
                  <>
                    {" · "}
                    <button
                      type="button"
                      onClick={() => onOpen(p)}
                      className="font-medium text-brand underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
                    >
                      Recommendations →
                    </button>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}

      {open && (
      <p className="mt-1.5 text-[10.5px] leading-snug text-ink-3">
        {data.forecast_published_at
          ? `Forecast published ${day(data.forecast_published_at.slice(0, 10))}. `
          : !forecastOnly && "No fresh forecast: dates use the burn rate. "}
        {!forecastOnly && data.counts_overdue > 0 && (
          <>
            {data.counts_overdue.toLocaleString("en-IN")} centre-and-medicine counts are already
            past their own run-out date — a count is overdue there.{" "}
          </>
        )}
        Synthetic data.
      </p>
      )}
    </section>
  );
}

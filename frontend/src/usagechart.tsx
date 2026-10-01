import { useEffect, useState } from "react";

import { type Usage, api } from "./api";

/**
 * Fix #84: the forecast made visible. One medicine at one centre: each of the
 * last 28 days' use (bars), the burn rate across the whole window (dashed),
 * and — where the shared model covers this state and medicine and its
 * forecast is fresh — the model's next-7-day rate (solid). Identity never rests
 * on colour alone: bars against lines, dashed against solid, a legend with the
 * values, and a table view. The palette is the portal's navy and grays.
 */

const PAST = 28;
const FUTURE = 7;
const W = 320;
const H = 84;
const TOP = 8;
const BAR_GAP = 2;

const C = {
  bar: "#9fb2c3",
  burn: "#4a525c",
  forecast: "#0b3d5c",
  grid: "#e3e6ea",
};

function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function rate(n: number | null): string {
  if (n === null) return "—";
  return n >= 100 ? Math.round(n).toLocaleString("en-IN") : n.toFixed(1);
}

export default function UsageChart({ facilityId, sku }: { facilityId: string; sku: string }) {
  const [u, setU] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .usage(facilityId, sku)
      .then((x) => alive && setU(x))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [facilityId, sku]);

  if (error) return <p className="text-[11px] text-ink-3">Use history could not be loaded.</p>;
  if (!u) return <p className="text-[11px] text-ink-3">Loading use history…</p>;

  const forecast = u.forecast_fresh ? u.forecast_daily : null;
  const values = u.days.map((d) => d.used ?? 0);
  const maxY = Math.max(1, ...values, u.burn_rate ?? 0, forecast ?? 0) * 1.08;
  const slot = W / (PAST + FUTURE);
  const barW = Math.min(24, slot - BAR_GAP);
  const y = (v: number) => TOP + (H - TOP) * (1 - v / maxY);
  const todayX = PAST * slot;
  const counted = u.days.filter((d) => d.used !== null).length;
  const hovered = hover !== null ? u.days[hover] : null;

  const summary =
    `Daily use of ${u.sku_name} at this centre over the last ${PAST} days: ${counted} days covered by counts. ` +
    `Burn rate ${rate(u.burn_rate)} a day.` +
    (forecast !== null ? ` Shared model's forecast for the next ${FUTURE} days: ${rate(forecast)} a day.` : "");

  return (
    <div className="mt-1">
      <svg viewBox={`0 0 ${W} ${H + 12}`} className="block h-auto w-full" role="img" aria-label={summary}>
        <line x1={0} x2={W} y1={H} y2={H} stroke={C.grid} strokeWidth={1} />
        <line x1={0} x2={W} y1={y(maxY / 1.08)} y2={y(maxY / 1.08)} stroke={C.grid} strokeWidth={1} />
        {u.days.map((d, i) => {
          if (d.used === null) return null;
          const h = Math.max(0, H - y(d.used));
          const x = i * slot + (slot - barW) / 2;
          const r = Math.min(4, barW / 2, h);
          // Rounded at the data end, square at the baseline.
          const path =
            h <= 0
              ? ""
              : `M${x},${H} L${x},${H - h + r} Q${x},${H - h} ${x + r},${H - h} L${x + barW - r},${H - h} Q${x + barW},${H - h} ${x + barW},${H - h + r} L${x + barW},${H} Z`;
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              {/* A hit target taller than the bar, so short bars are easy to hover. */}
              <rect x={i * slot} y={TOP} width={slot} height={H - TOP} fill="transparent" />
              {path && <path d={path} fill={C.bar} opacity={d.spread ? 0.5 : 1} />}
            </g>
          );
        })}
        <line x1={todayX} x2={todayX} y1={TOP - 4} y2={H} stroke={C.grid} strokeWidth={1} />
        <text x={todayX + 2} y={H + 10} className="fill-ink-3" fontSize={8}>
          today
        </text>
        <text x={0} y={H + 10} className="fill-ink-3" fontSize={8}>
          {dayLabel(u.days[0].day)}
        </text>
        {u.burn_rate !== null && (
          <line
            x1={0}
            x2={W}
            y1={y(u.burn_rate)}
            y2={y(u.burn_rate)}
            stroke={C.burn}
            strokeWidth={2}
            strokeDasharray="4 3"
            strokeLinecap="round"
          />
        )}
        {forecast !== null && (
          <line
            x1={todayX}
            x2={W}
            y1={y(forecast)}
            y2={y(forecast)}
            stroke={C.forecast}
            strokeWidth={2}
            strokeLinecap="round"
          />
        )}
      </svg>

      <p className="mt-0.5 min-h-[15px] text-[10.5px] text-ink-2" aria-live="polite">
        {hovered
          ? `${dayLabel(hovered.day)}: ${rate(hovered.used)} ${u.unit} used${
              hovered.spread ? " (a decline spread evenly over the days between two counts)" : ""
            }`
          : `Highest day ${rate(Math.max(0, ...values))} ${u.unit}`}
      </p>

      <ul className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] text-ink-2">
        <li className="flex items-center gap-1">
          <span aria-hidden="true" className="inline-block h-2.5 w-2 rounded-t-sm" style={{ background: C.bar }} />
          daily use
        </li>
        <li className="flex items-center gap-1">
          <svg aria-hidden="true" width="14" height="4">
            <line x1="1" x2="13" y1="2" y2="2" stroke={C.burn} strokeWidth="2" strokeDasharray="4 3" />
          </svg>
          burn rate {rate(u.burn_rate)}/day
        </li>
        {forecast !== null && (
          <li className="flex items-center gap-1">
            <svg aria-hidden="true" width="14" height="4">
              <line x1="1" x2="13" y1="2" y2="2" stroke={C.forecast} strokeWidth="2" />
            </svg>
            shared model {rate(forecast)}/day, next {FUTURE} days
          </li>
        )}
      </ul>
      <p className="mt-1 text-[10.5px] leading-snug text-ink-3">{u.note}</p>

      <details className="mt-1 text-[10.5px] text-ink-2">
        <summary className="cursor-pointer text-brand">Show as table</summary>
        <table className="mt-1 w-full">
          <thead>
            <tr className="text-left text-ink-3">
              <th className="font-medium">Day</th>
              <th className="text-right font-medium">Used ({u.unit})</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {u.days.map((d) => (
              <tr key={d.day} className="border-t border-line">
                <td className="font-sans">{dayLabel(d.day)}</td>
                <td className="text-right">
                  {d.used === null ? "no count" : rate(d.used)}
                  {d.spread ? " *" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-0.5 text-ink-3">* spread evenly over the days between two counts.</p>
      </details>
    </div>
  );
}

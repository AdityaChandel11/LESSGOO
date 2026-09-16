/**
 * Outbreak pre-positioning — spec 12.5.
 *
 * The control is deliberately plain, because what it does is not: declaring an
 * outbreak raises expected demand for the commodities that disease consumes
 * inside a radius, and everything downstream reacts on its own. Days of cover
 * fall, facilities turn amber and red, and the existing solver proposes
 * transfers toward the area — before a single facility has reported running
 * out.
 *
 * The number worth showing afterwards is not "an outbreak was declared" but
 * how many days sooner the worst-affected facility became visible as at risk.
 * That is the difference between this and a reactive system, and it is
 * measured from the same days-of-cover figures the map already draws.
 */

import { useEffect, useState } from "react";
import { ApiError, api, type Outbreak, type OutbreakCategory, type OutbreakDeclared } from "./api";

export function OutbreakControl({
  state,
  stateLabel,
  district,
  canDeclare,
  onChanged,
}: {
  state: string | null;
  stateLabel: string;
  /** Where the map is pointed: the centre of a declaration. */
  district: string | null;
  canDeclare: boolean;
  onChanged: () => void;
}) {
  const [categories, setCategories] = useState<Record<string, OutbreakCategory>>({});
  const [active, setActive] = useState<Outbreak[]>([]);
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("acute_diarrheal_disease");
  const [radius, setRadius] = useState(40);
  const [severity, setSeverity] = useState(0.7);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OutbreakDeclared | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.outbreakCategories().then(setCategories).catch(() => setCategories({}));
  }, []);

  const refresh = () =>
    api
      .outbreaksActive()
      .then(setActive)
      .catch(() => setActive([]));

  useEffect(() => {
    refresh();
  }, []);

  const declare = async () => {
    if (!state || !district) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.declareOutbreak({
        category,
        district,
        state,
        radius_km: radius,
        severity,
      });
      setResult(r);
      await refresh();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not declare that outbreak");
    } finally {
      setBusy(false);
    }
  };

  const withdraw = async (id: number) => {
    setBusy(true);
    try {
      await api.clearOutbreak(id);
      setResult(null);
      await refresh();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  if (!active.length && !open) {
    return (
      <button
        onClick={() => setOpen(true)}
        disabled={!canDeclare}
        title={
          canDeclare
            ? "Raise expected demand across a district and let the solver pre-position stock"
            : "Only this state's officers can declare an outbreak"
        }
        className="rounded-md border border-line px-2 py-1 text-[11.5px] font-medium text-ink-2 hover:border-crit hover:text-crit disabled:opacity-40"
      >
        Declare outbreak
      </button>
    );
  }

  return (
    <div className="w-[300px] rounded-lg border border-line bg-panel p-2.5 shadow-sm">
      {active.map((o) => (
        <div key={o.id} className="mb-2 rounded-md border border-crit/30 bg-crit/10 p-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[12.5px] font-semibold text-crit">{o.label}</div>
              <div className="text-[11.5px] text-ink-2">
                {o.district} · {o.radius_km} km · {o.facilities_affected} facilities
              </div>
            </div>
            <button
              onClick={() => void withdraw(o.id)}
              disabled={busy}
              className="shrink-0 text-[11px] font-medium text-brand hover:underline disabled:opacity-50"
            >
              Withdraw
            </button>
          </div>
          <div className="mt-1 text-[11px] text-ink-2">
            Expecting more {o.commodities.join(", ")} here until{" "}
            {o.expires_at ? new Date(o.expires_at).toLocaleDateString("en-IN") : "—"}.
          </div>
        </div>
      ))}

      {result && (
        <div className="mb-2 rounded-md border border-line bg-canvas p-2">
          <div className="text-[12px] text-ink">
            {result.at_risk_after - result.at_risk_before > 0 ? (
              <>
                <span className="font-semibold">
                  {result.at_risk_after - result.at_risk_before} more facilities
                </span>{" "}
                now show under a week of cover in {stateLabel}.
              </>
            ) : (
              <>No facility crossed a threshold — the district has enough on hand.</>
            )}
          </div>
          {result.earliest_warning_days != null && result.earliest_warning_days > 0 && (
            <div className="mt-1 text-[11.5px] text-ink-2">
              The worst-affected is visible as at risk{" "}
              <span className="font-semibold text-crit">
                {result.earliest_warning_days} days sooner
              </span>{" "}
              than its old rate suggested. A reactive system would have moved stock after the
              first stockout was reported.
            </div>
          )}
          <div className="mt-1 text-[11px] text-ink-3">
            Run Redistribution to see what the solver proposes. Every transfer still needs an
            officer's approval.
          </div>
        </div>
      )}

      {open && (
        <>
          <label className="block text-[11px] font-medium text-ink-2">
            Disease
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-0.5 w-full rounded border border-line bg-panel px-2 py-1 text-[12.5px] text-ink"
            >
              {Object.entries(categories).map(([key, c]) => (
                <option key={key} value={key}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          {categories[category] && (
            <p className="mt-1 text-[11px] text-ink-3">
              Raises demand for{" "}
              {Object.entries(categories[category].commodities)
                .map(([sku, m]) => `${sku} ×${m}`)
                .join(", ")}
              {" — a clinical table in source control, not a model output."}
            </p>
          )}
          <div className="mt-1.5 grid grid-cols-2 gap-2">
            <label className="block text-[11px] font-medium text-ink-2">
              Radius {radius} km
              <input
                type="range"
                min={10}
                max={150}
                step={5}
                value={radius}
                onChange={(e) => setRadius(Number(e.target.value))}
                className="mt-1 w-full"
              />
            </label>
            <label className="block text-[11px] font-medium text-ink-2">
              Severity {Math.round(severity * 100)}%
              <input
                type="range"
                min={0.1}
                max={1}
                step={0.1}
                value={severity}
                onChange={(e) => setSeverity(Number(e.target.value))}
                className="mt-1 w-full"
              />
            </label>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={declare}
              disabled={busy || !state || !district}
              className="rounded bg-crit px-3 py-1 text-[12.5px] font-medium text-white disabled:opacity-50"
            >
              {busy ? "Declaring…" : `Declare in ${district ?? "…"}`}
            </button>
            <button
              onClick={() => setOpen(false)}
              className="text-[11.5px] text-ink-2 hover:text-ink"
            >
              Cancel
            </button>
          </div>
          {!district && (
            <p className="mt-1 text-[11px] text-ink-3">
              Zoom to a district first — a declaration needs a centre.
            </p>
          )}
          {error && <p className="mt-1 text-[11.5px] text-crit">{error}</p>}
        </>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import {
  type ActiveOutbreak,
  type ActiveOutbreaks,
  ApiError,
  type Outbreaks,
  type StockingAdvice,
  type User,
  api,
  can,
  inDemoSandbox,
} from "./api";

const SHOWN = 3;
const WARNINGS_SHOWN = 5;

function day(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })
    : "—";
}

/**
 * Outbreaks, in two parts.
 *
 * Active outbreaks (fix #41, spec v3 §12.5) are the ones that change supply:
 * each raises the expected use of the medicines its disease drives in its
 * district for two weeks, and the same redistribution solver proposes
 * pre-positioning trips that a person approves. The size of the rise is the
 * district's observed rise where its readings show one, otherwise the
 * declaring officer's expectation — and the panel says which.
 *
 * Below them, the IDSP Weekly Outbreak Report (NCDC) rows, parsed once from the
 * published PDFs; the report weeks are printed so an old report is never taken
 * for this week's.
 */
export function OutbreakWarnings({
  state,
  stateLabel,
  user,
  refreshKey,
  onOpenTransfers,
}: {
  state: string | null;
  stateLabel: string;
  user: User;
  refreshKey: number;
  onOpenTransfers: () => void;
}) {
  const [data, setData] = useState<Outbreaks | null>(null);
  const [active, setActive] = useState<ActiveOutbreaks | null>(null);
  const [bump, setBump] = useState(0);
  const [open, setOpen] = useState(false);
  const [advice, setAdvice] = useState<StockingAdvice[] | null>(null);
  const [showAdvice, setShowAdvice] = useState(false);

  useEffect(() => {
    api.outbreaks(state).then(setData).catch(() => setData(null));
  }, [state]);

  useEffect(() => {
    let alive = true;
    api
      .activeOutbreaks(state)
      .then((a) => alive && setActive(a))
      .catch(() => alive && setActive(null));
    return () => {
      alive = false;
    };
  }, [state, refreshKey, bump]);

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

  const mayDeclare = !!state && can.planState(user, state);

  return (
    <section className="shrink-0 border-b border-line bg-panel px-3 py-2.5" aria-label="Outbreak warnings">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[12.5px] font-semibold text-ink">
          Active outbreaks · सक्रिय प्रकोप{" "}
          <span className="font-mono text-[11px] font-normal text-ink-3">
            {active?.outbreaks.length ?? 0}
          </span>
        </h2>
        <span className="text-[10.5px] text-ink-3">{stateLabel}</span>
      </div>

      {active && active.outbreaks.length === 0 && (
        <p className="mt-1 text-[11.5px] text-ink-2">
          No active outbreak {state ? `in ${stateLabel}` : "anywhere in the network"}. An active outbreak
          raises the expected use of the medicines its disease drives in its district for{" "}
          {active.ttl_days} days, and the redistribution plan pre-positions stock there.
        </p>
      )}

      {active?.outbreaks.map((o) => (
        <ActiveCard
          key={o.id}
          o={o}
          ttlDays={active.ttl_days}
          mayEnd={can.planState(user, o.state) && inDemoSandbox(user, o.state, o.district)}
          onEnded={() => setBump((b) => b + 1)}
        />
      ))}

      {mayDeclare && active && state && (
        <DeclareForm
          state={state}
          user={user}
          active={active}
          onDeclared={() => setBump((b) => b + 1)}
          onOpenTransfers={onOpenTransfers}
        />
      )}

      {data && (
        <div className="mt-3 border-t border-line pt-2">
          <h3 className="text-[12px] font-semibold text-ink">
            IDSP outbreak reports · आईडीएसपी रिपोर्ट{" "}
            <span className="font-mono text-[11px] font-normal text-ink-3">{data.rows.length}</span>
          </h3>
          <p className="mt-0.5 text-[10.5px] text-ink-3">
            Source:{" "}
            <a href={data.source_url} target="_blank" rel="noreferrer" className="underline hover:text-brand">
              {data.source}
            </a>{" "}
            · {data.reports.map((r) => `week ${r.week}/${r.year}`).join(", ")}
          </p>
          <IdspTable data={data} state={state} stateLabel={stateLabel} open={open} />
          <div className="mt-1.5 flex gap-3">
            <button
              onClick={() => setShowAdvice((s) => !s)}
              className="text-[11.5px] font-medium text-brand hover:underline"
            >
              {showAdvice ? "Hide stocking advice" : "Stocking advice →"}
            </button>
            {data.rows.length > SHOWN && (
              <button onClick={() => setOpen((o) => !o)} className="text-[11.5px] font-medium text-brand hover:underline">
                {open ? "Show fewer" : `Show all ${data.rows.length}`}
              </button>
            )}
          </div>
          {showAdvice && <Advice advice={advice} />}
        </div>
      )}
    </section>
  );
}

function ActiveCard({
  o,
  ttlDays,
  mayEnd,
  onEnded,
}: {
  o: ActiveOutbreak;
  ttlDays: number;
  mayEnd: boolean;
  onEnded: () => void;
}) {
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = all ? o.warnings : o.warnings.slice(0, WARNINGS_SHOWN);

  const end = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.endOutbreak(o.id);
      onEnded();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not end the outbreak");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="mt-2 rounded-md border border-line bg-canvas px-2.5 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[12px] font-semibold text-ink">
          {o.disease} · {o.district}
          <span className="font-normal text-ink-3">, {o.state}</span>
        </p>
        {mayEnd && (
          <button
            onClick={end}
            disabled={busy}
            className="shrink-0 text-[11px] font-medium text-brand hover:underline disabled:text-ink-3"
          >
            {busy ? "Ending…" : "End outbreak"}
          </button>
        )}
      </div>
      <p className="mt-0.5 text-[10.5px] text-ink-3">
        {o.source === "idsp" ? `IDSP report ${o.source_ref ?? ""}` : `Declared by ${o.declared_by ?? "an officer"}`} on{" "}
        {day(o.declared_at)} · lapses {day(o.expires_at)} ({ttlDays} days) · {o.facilities} centres in the district
      </p>

      <ul className="mt-1.5 flex flex-col gap-1">
        {o.medicines.map((m) => (
          <li key={m.sku_code} className="text-[11px] leading-snug">
            <span className="font-medium text-ink">{m.sku_name}</span>
            {m.multiplier !== null && (
              <span className="font-mono tabular-nums text-ink"> ×{m.multiplier.toFixed(2)}</span>
            )}
            {m.basis && (
              <span
                className={`ml-1 rounded border px-1 text-[9.5px] font-medium ${
                  m.basis === "observed" ? "border-brand/40 text-brand" : "border-risk/40 text-risk"
                }`}
              >
                {m.basis === "observed" ? "observed" : "assumption"}
              </span>
            )}
            <span className="block text-ink-3">{m.detail}</span>
          </li>
        ))}
      </ul>

      <p className="mt-1.5 text-[11px] font-medium text-ink-2">
        {o.warnings.length === 0
          ? `No centre runs out within ${ttlDays} days at the outbreak rate.`
          : `${o.warnings.length} centre–medicine pairs run out within ${ttlDays} days at the outbreak rate:`}
      </p>
      {shown.length > 0 && (
        <ul className="mt-0.5 flex flex-col gap-0.5">
          {shown.map((w) => (
            <li key={`${w.facility_id}:${w.sku_code}`} className="text-[11px] text-ink">
              {w.line}
            </li>
          ))}
        </ul>
      )}
      {o.warnings.length > WARNINGS_SHOWN && (
        <button onClick={() => setAll((a) => !a)} className="mt-0.5 text-[11px] font-medium text-brand hover:underline">
          {all ? "Show fewer" : `Show all ${o.warnings.length}`}
        </button>
      )}
      {o.count_overdue > 0 && (
        <p className="mt-1 text-[11px] text-ink-2">
          {o.count_overdue} more would already be empty at the outbreak rate — their last count is too old to
          project. Ask those centres to count the shelf.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1 text-[11px] text-crit">
          {error}
        </p>
      )}
    </article>
  );
}

function DeclareForm({
  state,
  user,
  active,
  onDeclared,
  onOpenTransfers,
}: {
  state: string;
  user: User;
  active: ActiveOutbreaks;
  onDeclared: () => void;
  onOpenTransfers: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [districts, setDistricts] = useState<string[]>([]);
  const [district, setDistrict] = useState("");
  const [disease, setDisease] = useState("");
  const [surge, setSurge] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    // A public demo account may declare only inside its sandbox district.
    if (user.demo_sandbox) {
      setDistricts(user.demo_sandbox.state === state ? [user.demo_sandbox.district] : []);
      return;
    }
    api
      .districts(null, state)
      .then((b) => alive && setDistricts(b.map((x) => x.label).sort()))
      .catch(() => alive && setDistricts([]));
    return () => {
      alive = false;
    };
  }, [open, state, user.demo_sandbox]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const pct = surge.trim() === "" ? null : Number(surge);
      const out = await api.declareOutbreak({ state, district, disease, surge_pct: pct });
      setDone(
        `Declared. The plan for its medicines was recomputed: ${out.trips_proposed} trips proposed, ` +
          `${out.pre_positioning_trips} of them pre-positioning for this outbreak. Each still needs the donor centre's yes.`,
      );
      onDeclared();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not declare the outbreak");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="mt-2 text-[11.5px] font-medium text-brand hover:underline">
        Declare an outbreak · प्रकोप घोषित करें →
      </button>
    );
  }

  const surgeValue = surge.trim() === "" ? null : Number(surge);
  const surgeValid =
    surgeValue === null || (Number.isFinite(surgeValue) && surgeValue >= 0 && surgeValue <= active.max_surge_pct);

  return (
    <div className="mt-2 rounded-md border border-line bg-canvas px-2.5 py-2">
      <h3 className="text-[12px] font-semibold text-ink">Declare an outbreak · प्रकोप घोषित करें</h3>
      <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        <label className="text-[11px] text-ink-2">
          District
          <select
            value={district}
            onChange={(e) => setDistrict(e.target.value)}
            className="mt-0.5 block w-full rounded border border-line bg-panel px-1.5 py-1 text-[12px] text-ink"
          >
            <option value="">Choose…</option>
            {districts.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-ink-2">
          Disease
          <select
            value={disease}
            onChange={(e) => setDisease(e.target.value)}
            className="mt-0.5 block w-full rounded border border-line bg-panel px-1.5 py-1 text-[12px] text-ink"
          >
            <option value="">Choose…</option>
            {active.diseases.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-ink-2 sm:col-span-2">
          Expected surge, % (optional)
          <input
            inputMode="numeric"
            value={surge}
            onChange={(e) => setSurge(e.target.value)}
            placeholder="e.g. 50"
            className="mt-0.5 block w-full rounded border border-line bg-panel px-1.5 py-1 text-[12px] text-ink"
          />
          <span className="mt-0.5 block text-[10.5px] leading-snug text-ink-3">
            Used only if the district's own readings show no rise of at least {active.min_rise_pct}% over the last{" "}
            {active.window_days} days — and then shown everywhere as your assumption, not a measurement.
          </span>
        </label>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={submit}
          disabled={busy || !district || !disease || !surgeValid}
          className="min-h-9 rounded-md bg-brand px-3 text-[12px] font-medium text-white disabled:bg-ink-3"
        >
          {busy ? "Declaring…" : "Declare and re-plan"}
        </button>
        <button onClick={() => setOpen(false)} className="text-[11.5px] font-medium text-ink-2 hover:underline">
          Close
        </button>
      </div>
      {!surgeValid && (
        <p className="mt-1 text-[11px] text-crit">Enter a surge between 0 and {active.max_surge_pct}%.</p>
      )}
      {error && (
        <p role="alert" className="mt-1 text-[11px] text-crit">
          {error}
        </p>
      )}
      {done && (
        <p className="mt-1 text-[11px] text-ink">
          {done}{" "}
          <button onClick={onOpenTransfers} className="font-medium text-brand hover:underline">
            Open the plan →
          </button>
        </p>
      )}
    </div>
  );
}

function IdspTable({
  data,
  state,
  stateLabel,
  open,
}: {
  data: Outbreaks;
  state: string | null;
  stateLabel: string;
  open: boolean;
}) {
  if (data.rows.length === 0) {
    return <p className="mt-1.5 text-[11.5px] text-ink-2">No outbreak in these reports for {stateLabel}.</p>;
  }
  const rows = open ? data.rows : data.rows.slice(0, SHOWN);
  return (
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
          <tr
            key={r.unique_id}
            className="border-t border-line align-top"
            title={`${r.unique_id} · ${data.columns[3]}: ${day(r.start_date)}`}
          >
            <td className="py-1 font-medium text-ink">{r.disease}</td>
            <td className="py-1 text-ink-2">
              {r.district}
              {!state && <span className="text-ink-3">, {r.state_code ?? r.state}</span>}
              {r.in_network && <span className="ml-1 text-[10px] font-medium text-brand">in network</span>}
            </td>
            <td className="py-1 text-right font-mono tabular-nums text-ink">{r.cases}</td>
            <td className={`py-1 text-right font-mono tabular-nums ${r.deaths ? "text-crit" : "text-ink-3"}`}>
              {r.deaths}
            </td>
            <td className="py-1 pl-2 text-ink-2">{r.status ?? "not stated"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Advice({ advice }: { advice: StockingAdvice[] | null }) {
  return (
    <div className="mt-2 rounded-md border border-line bg-canvas px-2.5 py-2">
      <p className="text-[10.5px] leading-snug text-ink-3">
        <span className="font-semibold text-ink-2">How it is built: </span>
        IDSP weekly report → parsed outbreak rows → disease-to-medicine map + monsoon calendar → declare the
        outbreak → the district's observed rise (or your stated expectation) → redistribution plan → a person
        approves each trip.
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
  );
}

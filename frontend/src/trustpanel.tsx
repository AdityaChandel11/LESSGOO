/**
 * The trust layer's two faces — spec 12.6.
 *
 * `TrustBlock`   in the facility drawer: what this facility's signals say about
 *                each other, and the one consequence that follows.
 * `AuditQueue`   for a district or state officer: where a physical visit is
 *                worth the trip, ranked.
 *
 * Both are worded as an invitation to check, never as an accusation. The most
 * common true explanation for a flagged facility is a register nobody had time
 * to fill in, and the interface has to read that way or officers will stop
 * trusting it in the other direction.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  SIGNAL_LABEL,
  TRUST_BAND,
  type AuditRow,
  type Bucket,
  type Trust,
  type TrustComponent,
  api,
} from "./api";
import { WhyLine } from "./why";

function Bar({
  c,
  onOpenEvidence,
}: {
  c: TrustComponent;
  onOpenEvidence?: (e: NonNullable<TrustComponent["evidence"]>) => void;
}) {
  const share = Math.min(100, c.penalty * 100);
  const clean = c.penalty < 0.05;
  return (
    <li className="py-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11.5px] text-ink-2">{SIGNAL_LABEL[c.signal] ?? c.signal}</span>
        <span className={`font-mono text-[10.5px] ${clean ? "text-ok" : "text-crit"}`}>
          {clean ? "clear" : `−${Math.round(c.cost * 100)}`}
        </span>
      </div>
      <div className="relative mt-1 h-1 rounded-full bg-line">
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${Math.max(share, 2)}%`,
            background: clean ? "#1b9150" : share > 60 ? "#d92d20" : "#e0900e",
          }}
        />
      </div>
      {c.evidence && onOpenEvidence ? (
        <button
          onClick={() => onOpenEvidence(c.evidence!)}
          className="mt-0.5 text-left text-[11px] text-brand hover:underline"
        >
          {c.reason} <span aria-hidden="true">→</span>
        </button>
      ) : (
        <p className="mt-0.5 text-[11px] text-ink-3">{c.reason}</p>
      )}
    </li>
  );
}

export function TrustBlock({
  facilityId,
  refreshKey,
  onOpenEvidence,
}: {
  facilityId: string;
  /** Bumped by the dashboard's live feed — the trust panel refreshes on the
   *  same stream as everything else, never on a timer of its own. */
  refreshKey: number;
  onOpenEvidence?: (e: NonNullable<TrustComponent["evidence"]>) => void;
}) {
  const [trust, setTrust] = useState<Trust | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api
      .facilityTrust(facilityId)
      .then(setTrust)
      .catch(() => setTrust(null));
  }, [facilityId, refreshKey]);

  if (!trust) return null;
  const band = TRUST_BAND[trust.band];
  const widened = trust.warning_multiplier > 1.01;

  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
          Data confidence
        </span>
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-[11.5px] font-medium text-brand hover:underline"
        >
          {open ? "Hide" : "What this is built from"}
        </button>
      </div>

      <div className="mt-1.5 flex items-center gap-2">
        <span className="font-mono text-[19px] font-semibold tabular-nums text-ink">
          {Math.round(trust.score * 100)}
        </span>
        <span className="text-[12px] text-ink-2">out of 100</span>
        <span
          className={`ml-auto shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${band.className}`}
        >
          {band.label}
        </span>
      </div>

      {trust.components.some((c) => c.penalty >= 0.05) && (
        <WhyLine
          key={facilityId}
          load={() => api.explainTrust(facilityId)}
          label="Why do these signals disagree?"
        />
      )}

      <p className="mt-1 text-[11.5px] text-ink-2">
        {widened ? (
          <>
            Because these numbers are harder to rely on, this facility's stock warning trips{" "}
            <span className="font-medium">{trust.warning_multiplier.toFixed(1)}× earlier</span> — at{" "}
            {(7 * trust.warning_multiplier).toFixed(1)} days of cover instead of 7. The quantities
            it is sent are unchanged.
          </>
        ) : (
          "Its signals agree with each other, so the usual warning thresholds apply."
        )}
      </p>

      {open && (
        <>
          <ul className="mt-2">
            {trust.components.map((c) => (
              <Bar key={c.signal} c={c} onOpenEvidence={onOpenEvidence} />
            ))}
          </ul>
          <p className="mt-1.5 text-[11px] text-ink-3">
            Each signal is checked against the others rather than against a target, and is
            computed from the live tables when you open this panel — the underlined sentences
            open the very rows they were counted from. A flag is a reason to ask a question;
            most often the answer is a process that broke, not anyone acting badly.
          </p>
        </>
      )}
    </div>
  );
}

const SEARCH_CLASS =
  "w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-ink-3 focus:border-brand focus:bg-panel focus:outline-none";

export function AuditQueuePanel({
  stateLabel,
  state,
  states,
  refreshKey,
  onPick,
  onPickState,
}: {
  stateLabel: string;
  /** The scope actually requested, so the heading and the rows agree. Null is
   *  national, which only an administrator can be. */
  state: string | null;
  states: Bucket[];
  refreshKey: number;
  onPick: (row: AuditRow) => void;
  onPickState: (code: string) => void;
}) {
  // Scoring every facility in the country live took 46s against the deployed
  // database, so national asks for a state instead of scoring one.
  if (state == null) return <PickState states={states} onPickState={onPickState} />;
  return <AuditQueue state={state} stateLabel={stateLabel} refreshKey={refreshKey} onPick={onPick} />;
}

function PickState({
  states,
  onPickState,
}: {
  states: Bucket[];
  onPickState: (code: string) => void;
}) {
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return states
      .filter((s) => !q || s.label.toLowerCase().includes(q))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [states, query]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <h2 className="text-[13px] font-semibold text-ink">
          Where to look first · कहाँ पहले देखें
        </h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          The audit queue is scored live from each facility&rsquo;s last 14 days of records, one
          state at a time, so the ranking is never older than the rows behind it. Choose a state to
          score it.
        </p>
      </div>
      <div className="px-3 pt-2.5 pb-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search states"
          aria-label="Search states"
          className={SEARCH_CLASS}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {shown.map((s) => (
          <button
            key={s.key}
            onClick={() => onPickState(s.key)}
            className="flex w-full items-baseline justify-between gap-2 px-3 py-2 text-left hover:bg-canvas"
          >
            <span className="truncate text-[13px] font-medium text-ink">{s.label}</span>
            <span className="shrink-0 font-mono text-[11px] text-ink-3 tabular-nums">
              {s.total.toLocaleString("en-IN")} facilities
            </span>
          </button>
        ))}
        {shown.length === 0 && (
          <p className="px-3 py-6 text-center text-[12px] text-ink-3">No states match.</p>
        )}
      </div>
    </div>
  );
}

type SortKey = "confidence" | "district" | "name";

function AuditQueue({
  stateLabel,
  state,
  refreshKey,
  onPick,
}: {
  stateLabel: string;
  state: string;
  refreshKey: number;
  onPick: (row: AuditRow) => void;
}) {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("confidence");
  const inFlight = useRef(false);
  const again = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // The live feed bumps refreshKey every few seconds while scoring takes
  // longer than that; a bump mid-request becomes one follow-up, not a pile-up.
  const load = useCallback(
    function run() {
      if (inFlight.current) {
        again.current = true;
        return;
      }
      inFlight.current = true;
      setLoading(true);
      api
        .trustQueue(state)
        .then((r) => {
          if (!alive.current) return;
          setRows(r);
          setError(null);
          setLoaded(true);
        })
        .catch((e) => {
          if (!alive.current) return;
          setError(e instanceof ApiError ? e.message : "Could not load the audit queue");
        })
        .finally(() => {
          inFlight.current = false;
          if (!alive.current) return;
          setLoading(false);
          if (again.current) {
            again.current = false;
            run();
          }
        });
    },
    [state],
  );

  useEffect(load, [load, refreshKey]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = q
      ? rows.filter(
          (r) => r.facility_name.toLowerCase().includes(q) || r.district.toLowerCase().includes(q),
        )
      : rows;
    // The server's order is the spec's: lowest confidence first, larger
    // facilities first within a tie.
    if (sort === "confidence") return hit;
    const key = (r: AuditRow) =>
      sort === "district" ? `${r.district} ${r.facility_name}` : r.facility_name;
    return [...hit].sort((a, b) => key(a).localeCompare(key(b)));
  }, [rows, query, sort]);

  const audit = rows.filter((r) => r.band === "audit").length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-[13px] font-semibold text-ink">Where to look first — {stateLabel}</h2>
          {loading && loaded && <span className="shrink-0 text-[11px] text-ink-3">Updating…</span>}
        </div>
        <p className="mt-0.5 text-[12px] text-ink-2">
          Facilities whose own signals disagree with each other: attendance against patients seen,
          ward photos against the register, deliveries against confirmations. Ranked so a visit
          goes where it is most likely to find something.
        </p>
        {audit > 0 && (
          <p className="mt-1.5 text-[12px] font-medium text-crit">
            {audit === 1
              ? "1 facility needs a visit before its numbers can be relied on."
              : `${audit} facilities need a visit before their numbers can be relied on.`}
          </p>
        )}
        {error && rows.length > 0 && (
          <p className="mt-1.5 text-[12px] text-crit">Could not refresh: {error}</p>
        )}
      </div>

      {rows.length > 0 && (
        <div className="border-b border-line px-3 pt-2.5 pb-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search facility or district"
            aria-label="Search the audit queue"
            className={SEARCH_CLASS}
          />
          <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-ink-3">
            <span>
              {shown.length === rows.length
                ? `${rows.length} flagged`
                : `${shown.length} of ${rows.length} flagged`}
              {rows.length >= 50 && " · the 50 lowest"}
            </span>
            <label className="flex items-center gap-1.5">
              Sort
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                className="h-6 rounded border border-line bg-panel px-1 text-[11px] text-ink focus:border-brand focus:outline-none"
              >
                <option value="confidence">Lowest confidence first</option>
                <option value="district">District A–Z</option>
                <option value="name">Facility A–Z</option>
              </select>
            </label>
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {error && rows.length === 0 ? (
          <p className="p-3 text-[12.5px] text-crit">{error}</p>
        ) : !loaded ? (
          <p className="p-3 text-[12.5px] text-ink-3">
            Scoring {stateLabel}&rsquo;s facilities from the last 14 days of records…
          </p>
        ) : rows.length === 0 ? (
          <p className="p-3 text-[12.5px] text-ink-2">
            Nothing to review — every facility's signals currently agree with each other.
          </p>
        ) : shown.length === 0 ? (
          <p className="px-3 py-6 text-center text-[12px] text-ink-3">No facilities match.</p>
        ) : (
          shown.map((r) => {
            const band = TRUST_BAND[r.band];
            const isOpen = expanded === r.facility_id;
            return (
              <div key={r.facility_id} className="border-b border-line px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <span
                    className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
                    style={{ background: band.dot }}
                  />
                  <div className="min-w-0 flex-1">
                    <button
                      onClick={() => onPick(r)}
                      className="block truncate text-left text-[13px] font-medium text-ink hover:text-brand hover:underline"
                    >
                      {r.facility_name}
                    </button>
                    <p className="text-[11.5px] text-ink-3">
                      {r.district} · {r.type}
                    </p>
                  </div>
                  <span className="shrink-0 font-mono text-[13px] font-semibold tabular-nums text-ink-2">
                    {Math.round(r.score * 100)}
                  </span>
                </div>

                <p className="mt-1 text-[12px] text-ink-2">
                  {r.components[0]?.reason ?? "Signals disagree."}
                </p>

                <WhyLine
                  load={() => api.explainTrust(r.facility_id)}
                  label="Why do these signals disagree?"
                />

                {isOpen && (
                  <ul className="mt-1.5">
                    {r.components.slice(1).map((c) => (
                      <li key={c.signal} className="text-[11.5px] text-ink-3">
                        {c.reason}
                      </li>
                    ))}
                  </ul>
                )}

                {r.components.length > 1 && (
                  <button
                    onClick={() => setExpanded(isOpen ? null : r.facility_id)}
                    className="mt-1 text-[11.5px] font-medium text-brand hover:underline"
                  >
                    {isOpen ? "Less" : `${r.components.length - 1} more signal${r.components.length > 2 ? "s" : ""}`}
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";

import { AttendancePanel } from "./attendancepanel";
import { BedPanel } from "./bedpanel";
import { TrustBlock } from "./trustpanel";
import {
  type Bucket,
  type FacilityDetail,
  type LiveEvent,
  type Pin,
  type Sku,
  type Status,
  type Summary,
  type User,
  STATUS_COLOR,
  STATUS_LABEL,
  STATUS_RULE,
  api,
  bucketSeverity,
  formatDays,
  formatLatency,
  pct,
} from "./api";

/* ============================================================ primitives === */

export function StackBar({
  critical,
  at_risk,
  healthy,
  height = 6,
}: {
  critical: number;
  at_risk: number;
  healthy: number;
  height?: number;
}) {
  const total = critical + at_risk + healthy;
  if (!total) return <div className="rounded-full bg-line" style={{ height }} />;
  return (
    <div
      className="flex w-full overflow-hidden rounded-full bg-line"
      style={{ height }}
      role="img"
      aria-label={`${critical} critical, ${at_risk} at risk, ${healthy} adequate`}
    >
      {([["critical", critical], ["at_risk", at_risk], ["healthy", healthy]] as const).map(
        ([s, n]) =>
          n > 0 && (
            <span key={s} style={{ width: `${(100 * n) / total}%`, background: STATUS_COLOR[s] }} />
          ),
      )}
    </div>
  );
}

export function StatusPill({ status }: { status: Status }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ background: `${STATUS_COLOR[status]}17`, color: STATUS_COLOR[status] }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: STATUS_COLOR[status] }} />
      {STATUS_LABEL[status]}
    </span>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
      {children}
    </div>
  );
}

function Kpis({ s }: { s: Pick<Summary, "critical" | "at_risk" | "healthy" | "facilities"> }) {
  return (
    <div>
      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line">
        {(["critical", "at_risk", "healthy"] as Status[]).map((st) => {
          const n = st === "critical" ? s.critical : st === "at_risk" ? s.at_risk : s.healthy;
          return (
            <div key={st} className="bg-panel px-3 py-2.5">
              <div
                className="font-mono text-[22px] leading-none font-semibold tabular-nums"
                style={{ color: STATUS_COLOR[st] }}
              >
                {n.toLocaleString("en-IN")}
              </div>
              <div className="mt-1.5 text-[11.5px] font-medium text-ink">
                {STATUS_LABEL[st]}{" "}
                <span className="font-normal text-ink-3">{pct(n, s.facilities)}</span>
              </div>
              <div className="text-[10.5px] text-ink-3">{STATUS_RULE[st]}</div>
            </div>
          );
        })}
      </div>
      <div className="mt-2.5">
        <StackBar critical={s.critical} at_risk={s.at_risk} healthy={s.healthy} height={8} />
      </div>
    </div>
  );
}

function BucketRow({
  b,
  onClick,
  rankNo,
}: {
  b: Bucket;
  onClick: () => void;
  rankNo: number;
}) {
  const sev = bucketSeverity(b);
  return (
    <button
      onClick={onClick}
      className="group grid w-full grid-cols-[20px_1fr_auto] items-center gap-x-3 px-4 py-2 text-left hover:bg-canvas focus-visible:bg-canvas focus-visible:outline-none"
    >
      <span className="font-mono text-[11px] text-ink-3 tabular-nums">{rankNo}</span>
      <div className="min-w-0">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-[13px] font-medium text-ink group-hover:text-brand">
            {b.label}
          </span>
          <span className="shrink-0 font-mono text-[11px] text-ink-3 tabular-nums">
            of {b.total}
          </span>
        </div>
        <div className="mt-1.5">
          <StackBar critical={b.critical} at_risk={b.at_risk} healthy={b.healthy} height={5} />
        </div>
      </div>
      <div className="w-14 text-right">
        <div
          className="font-mono text-[13px] font-semibold tabular-nums"
          style={{ color: STATUS_COLOR[sev] }}
        >
          {b.critical}
        </div>
        <div className="font-mono text-[10px] text-ink-3 tabular-nums">
          {Math.round(b.critical_pct)}%
        </div>
      </div>
    </button>
  );
}

function medicineLabel(sku: string | null, skus: Sku[]): string {
  if (!sku) return "All medicines";
  return skus.find((s) => s.code === sku)?.name ?? sku;
}

/* ============================================================= national === */

export function NationalPanel({
  summary,
  states,
  sku,
  skus,
  onPickState,
}: {
  summary: Summary | null;
  states: Bucket[];
  sku: string | null;
  skus: Sku[];
  onPickState: (b: Bucket) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-4 pt-4 pb-4">
        <Eyebrow>National overview</Eyebrow>
        <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">India</h2>
        <p className="mt-0.5 text-[12.5px] text-ink-2">
          {summary
            ? `${summary.facilities.toLocaleString("en-IN")} facilities · ${summary.states} states & UTs · ${summary.districts} districts`
            : "Loading network…"}
        </p>
        <p className="mt-3 text-[12px] text-ink-2">
          Condition of <span className="font-medium text-ink">{medicineLabel(sku, skus)}</span>
          {!sku && <span className="text-ink-3"> — each facility's lowest-stocked item</span>}
        </p>
        {summary && (
          <div className="mt-2.5">
            <Kpis s={summary} />
          </div>
        )}
      </div>

      <div className="flex items-center justify-between px-4 pt-3 pb-1.5">
        <Eyebrow>States by facilities critical</Eyebrow>
        <span className="text-[10.5px] text-ink-3">count · share</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {states.map((b, i) => (
          <BucketRow key={b.key} b={b} rankNo={i + 1} onClick={() => onPickState(b)} />
        ))}
      </div>
    </div>
  );
}

/* ================================================================ state === */

export function StatePanel({
  stateCode,
  stateLabel,
  sku,
  skus,
  refreshKey,
  selectedFacilityId,
  onPickDistrict,
  onPickFacility,
}: {
  stateCode: string;
  stateLabel: string;
  sku: string | null;
  skus: Sku[];
  refreshKey: number;
  selectedFacilityId: string | null;
  onPickDistrict: (b: Bucket) => void;
  onPickFacility: (p: Pin) => void;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [districts, setDistricts] = useState<Bucket[]>([]);
  const [pins, setPins] = useState<Pin[]>([]);
  const [tab, setTab] = useState<"facilities" | "districts">("facilities");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.summary(sku, stateCode),
      api.districts(sku, stateCode),
      api.pinsInState(stateCode, sku),
    ])
      .then(([s, d, p]) => {
        if (!alive) return;
        setSummary(s);
        setDistricts(d);
        setPins(p);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [stateCode, sku, refreshKey]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return pins;
    return pins.filter(
      (p) => p.name.toLowerCase().includes(q) || p.district.toLowerCase().includes(q),
    );
  }, [pins, query]);

  const med = medicineLabel(sku, skus);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-4 pt-4 pb-4">
        <Eyebrow>State view</Eyebrow>
        <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">
          {stateLabel || summary?.state_name || stateCode}
        </h2>
        <p className="mt-0.5 text-[12.5px] text-ink-2">
          {summary
            ? `${summary.facilities} facilities · ${summary.phcs} PHCs · ${summary.chcs} CHCs · ${summary.districts} districts`
            : "Loading state data…"}
        </p>
        <p className="mt-3 text-[12px] text-ink-2">
          Condition of <span className="font-medium text-ink">{med}</span>
        </p>
        {summary && (
          <div className="mt-2.5">
            <Kpis s={summary} />
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 border-b border-line px-3 pt-2">
        {(["facilities", "districts"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-2.5 pb-2 text-[12.5px] font-medium capitalize ${
              tab === t
                ? "border-brand text-ink"
                : "border-transparent text-ink-3 hover:text-ink-2"
            }`}
          >
            {t}
            <span className="ml-1.5 font-mono text-[11px] text-ink-3">
              {t === "facilities" ? pins.length : districts.length}
            </span>
          </button>
        ))}
      </div>

      {tab === "districts" ? (
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {districts.map((b, i) => (
            <BucketRow key={b.key} b={b} rankNo={i + 1} onClick={() => onPickDistrict(b)} />
          ))}
        </div>
      ) : (
        <>
          <div className="px-4 pt-2.5 pb-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search facility or district"
              aria-label="Search facilities"
              className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-ink-3 focus:border-brand focus:bg-panel focus:outline-none"
            />
            <div className="mt-2 flex justify-between text-[10.5px] text-ink-3">
              <span>Most urgent first</span>
              <span>{sku ? `Days of ${sku}` : "Lowest cover"}</span>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pb-2">
            {filtered.map((p) => (
              <button
                key={p.id}
                onClick={() => onPickFacility(p)}
                className={`grid w-full grid-cols-[8px_1fr_auto] items-center gap-x-3 px-4 py-2 text-left hover:bg-canvas ${
                  p.id === selectedFacilityId ? "bg-canvas" : ""
                }`}
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: STATUS_COLOR[p.status] }}
                />
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-medium text-ink">{p.name}</div>
                  <div className="truncate text-[11px] text-ink-3">
                    {p.district} · {p.type}
                    {!sku && p.critical_skus > 0 && (
                      <span className="text-crit">
                        {" "}
                        · {p.critical_skus} medicine{p.critical_skus > 1 ? "s" : ""} critical
                      </span>
                    )}
                  </div>
                </div>
                <div
                  className="text-right font-mono text-[12.5px] font-semibold tabular-nums"
                  style={{ color: STATUS_COLOR[p.status] }}
                >
                  {formatDays(p.min_days)}
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="px-4 py-6 text-center text-[12px] text-ink-3">No facilities match.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ============================================================= facility === */

const DAYS_SCALE = 30;

function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs} h ago`;
  return `${Math.round(hrs / 24)} days ago`;
}

export function FacilityPanel({
  id,
  sku,
  skus,
  refreshKey,
  stateName,
  user,
  demoMode,
  onBack,
  onOpenEvidence,
}: {
  id: string;
  sku: string | null;
  skus: Sku[];
  refreshKey: number;
  stateName: string;
  user: User;
  demoMode: boolean;
  onBack: () => void;
  /** Follow a trust score's evidence to the rows it was computed from. */
  onOpenEvidence?: (
    evidence: { tab: string },
    facility: { id: string; name: string },
  ) => void;
}) {
  const [detail, setDetail] = useState<FacilityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .facility(id)
      .then((d) => {
        if (alive) {
          setDetail(d);
          setError(null);
        }
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [id, refreshKey]);

  const units = useMemo(() => new Map(skus.map((s) => [s.code, s.unit])), [skus]);

  const rows = useMemo(() => {
    if (!detail) return [];
    // The medicine being viewed on the map goes to the top of the list.
    return [...detail.skus].sort((a, b) => {
      if (sku) {
        if (a.sku_code === sku) return -1;
        if (b.sku_code === sku) return 1;
      }
      return (a.days_of_stock ?? 1e9) - (b.days_of_stock ?? 1e9);
    });
  }, [detail, sku]);

  const counts = useMemo(() => {
    const c = { critical: 0, at_risk: 0, healthy: 0 };
    for (const s of detail?.skus ?? []) c[s.status] += 1;
    return c;
  }, [detail]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-4 pt-3 pb-4">
        <button
          onClick={onBack}
          className="text-[12px] font-medium text-brand hover:underline"
        >
          ← {stateName}
        </button>
        {error && <p className="mt-3 text-[12px] text-crit">{error}</p>}
        {detail && (
          <>
            <div className="mt-3 flex items-center gap-2">
              <Eyebrow>{detail.type === "CHC" ? "Community Health Centre" : "Primary Health Centre"}</Eyebrow>
            </div>
            <h2 className="mt-1 text-[19px] leading-snug font-semibold tracking-tight text-ink">
              {detail.name}
            </h2>
            <p className="mt-0.5 text-[12px] text-ink-2">
              {detail.district}, {stateName}
            </p>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <StatusPill status={detail.status} />
              <span className="font-mono text-[11px] text-ink-3">{detail.id}</span>
            </div>
            <div className="mt-3.5 grid grid-cols-3 gap-2 text-center">
              {(["critical", "at_risk", "healthy"] as Status[]).map((st) => (
                <div key={st} className="rounded-md border border-line px-2 py-1.5">
                  <div
                    className="font-mono text-[17px] font-semibold tabular-nums"
                    style={{ color: STATUS_COLOR[st] }}
                  >
                    {counts[st]}
                  </div>
                  <div className="text-[10.5px] text-ink-3">{STATUS_LABEL[st].toLowerCase()}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {detail && (
        <>
          <TrustBlock
            facilityId={detail.id}
            refreshKey={refreshKey}
            onOpenEvidence={
              onOpenEvidence && ((e) => onOpenEvidence(e, { id: detail.id, name: detail.name }))
            }
          />
          <BedPanel
            facilityId={detail.id}
            facility={detail}
            user={user}
            refreshKey={refreshKey}
            demoMode={demoMode}
          />
          <AttendancePanel
            facilityId={detail.id}
            facility={detail}
            user={user}
            refreshKey={refreshKey}
            demoMode={demoMode}
          />
        </>
      )}

      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <Eyebrow>Medicine stock</Eyebrow>
        <span className="text-[10.5px] text-ink-3">days of cover at current use</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-3">
        {!detail && !error && <p className="px-4 py-6 text-[12px] text-ink-3">Loading…</p>}
        {rows.map((s) => {
          const focused = s.sku_code === sku;
          const width = Math.min(100, ((s.days_of_stock ?? 0) / DAYS_SCALE) * 100);
          return (
            <div
              key={s.sku_code}
              className={`px-4 py-2.5 ${focused ? "bg-brand/[0.06]" : ""} border-b border-line/60`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-[13px] font-medium text-ink">{s.sku_name}</span>
                  {s.is_controlled && (
                    <span
                      className="ml-1.5 rounded border border-line px-1 text-[9.5px] font-medium text-ink-2"
                      title="Controlled substance: never moved by automated transfer"
                    >
                      Controlled
                    </span>
                  )}
                  {s.cold_chain && (
                    <span
                      className="ml-1.5 rounded border border-line px-1 text-[9.5px] font-medium text-ink-2"
                      title="Requires cold chain"
                    >
                      Cold chain
                    </span>
                  )}
                </div>
                <span
                  className="shrink-0 font-mono text-[12.5px] font-semibold tabular-nums"
                  style={{ color: STATUS_COLOR[s.status] }}
                >
                  {formatDays(s.days_of_stock)}
                </span>
              </div>
              <div className="relative mt-1.5 h-1.5 rounded-full bg-line">
                <div
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{ width: `${Math.max(width, 1.5)}%`, background: STATUS_COLOR[s.status] }}
                />
                {/* threshold ticks at 3 and 7 days */}
                <span className="absolute inset-y-[-2px] w-px bg-ink-3/40" style={{ left: `${(3 / DAYS_SCALE) * 100}%` }} />
                <span className="absolute inset-y-[-2px] w-px bg-ink-3/40" style={{ left: `${(7 / DAYS_SCALE) * 100}%` }} />
              </div>
              <div className="mt-1 flex justify-between text-[10.5px] text-ink-3">
                <span className="font-mono tabular-nums">
                  {Math.round(s.qty_on_hand).toLocaleString("en-IN")} {units.get(s.sku_code) ?? ""}
                  {s.daily_burn_rate ? ` · uses ${s.daily_burn_rate.toFixed(1)}/day` : ""}
                  {s.rate_source === "federated" && (
                    <span
                      className="ml-1 rounded border border-brand/40 px-1 text-[9.5px] font-medium text-brand"
                      title="Next week's rate from the federated model, trained across states. Without a fresh forecast this falls back to the last 28 days of readings."
                    >
                      forecast
                    </span>
                  )}
                </span>
                <span>
                  {s.last_source === "seed" ? "baseline" : `via ${s.last_source}`} · {ago(s.last_reported_at)}
                </span>
              </div>
            </div>
          );
        })}

      </div>
    </div>
  );
}

/* ======================================================== field activity === */

export function ActivityFeed({
  events,
  onSimulate,
  canSimulate,
}: {
  events: LiveEvent[];
  onSimulate: () => void;
  canSimulate: boolean;
}) {
  const readings = events.filter((e) => e.kind === "reading.committed").slice(0, 3);
  return (
    <div className="border-t border-line bg-canvas/60 px-4 py-2.5">
      <div className="flex items-center justify-between">
        <Eyebrow>Field reports</Eyebrow>
        <button
          onClick={onSimulate}
          disabled={!canSimulate}
          className="text-[11px] font-medium text-brand hover:underline disabled:text-ink-3 disabled:no-underline"
          title="Sends a report through the same pipeline an SMS will use"
        >
          Send test report
        </button>
      </div>
      {readings.length === 0 ? (
        <p className="mt-1 text-[11.5px] text-ink-3">No reports this session yet.</p>
      ) : (
        <ul className="mt-1.5 space-y-1">
          {readings.map((e) => (
            <li key={e.seq} className="flex items-baseline gap-2 text-[11.5px]">
              <span className="truncate font-medium text-ink">{e.facility_name}</span>
              <span className="shrink-0 font-mono text-ink-2 tabular-nums">
                {e.sku_code} {e.qty_on_hand}
              </span>
              <span className="ml-auto shrink-0 text-ink-3">
                {e.source} · {formatLatency(e.latencyMs)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

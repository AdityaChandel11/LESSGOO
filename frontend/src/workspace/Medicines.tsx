/**
 * What this centre holds, and how it knows.
 *
 * One card per medicine, worst first — the same ordering the officer's panel
 * uses, for the same reason: the thing you are about to run out of should not
 * be below the fold. Each card answers three questions in order, because that
 * is the order a pharmacist asks them: how much is there, when does it run
 * out, and who says so.
 *
 * The third question is the one this screen exists for. A stock figure with no
 * provenance is a number somebody typed; a stock figure that says "confirmed
 * on delivery TRF-412, two days ago" is a number with a record behind it. The
 * card never upgrades what it knows: a seeded opening balance is described as
 * a seeded opening balance, not as a count.
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  type OwnRequest,
  type ProvenanceKind,
  type Supply,
  type WorkspaceSku,
  type WorkspaceView,
  TRUST_BAND,
  api,
  formatDays,
} from "../api";
import Briefing from "./Briefing";
import StockPhoto from "./StockPhoto";
import Team from "./Team";
import FindSupply from "./FindSupply";
import { both } from "./labels";
import UsageChart from "../usagechart";

const STATUS_STYLE: Record<string, { dot: string; text: string; label: string }> = {
  critical: { dot: "bg-crit", text: "text-crit", label: "Critical" },
  at_risk: { dot: "bg-risk", text: "text-risk", label: "At risk" },
  healthy: { dot: "bg-ok", text: "text-ok", label: "Healthy" },
  // Past the run-out date since the last count (fix #26): the real level is
  // unknown, so the card asks for a count rather than showing a colour.
  count_overdue: { dot: "bg-ink-2", text: "text-ink", label: "Count overdue" },
};

const PROVENANCE_LABEL: Record<ProvenanceKind, string> = {
  counted: "Counted by hand",
  photo: "Read from a photo",
  delivery: "Confirmed on delivery",
  phone: "Reported by phone",
  system: "Not yet verified",
  none: "Not yet reported",
};

function ago(days: number | null): string {
  if (days === null) return "";
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

function longDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** "12 min ago", "3 h ago" — how long a request has been waiting. */
function waited(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

/**
 * This centre's own request for the medicine, while it waits and after
 * (fix list #31). A request still waiting replaces "Find supply" — asking a
 * second donor for the same shortfall is refused anyway — and can be
 * cancelled here. One that closed says how, in words.
 */
function RequestChip({
  r,
  unit,
  onCancel,
}: {
  r: OwnRequest;
  unit: string;
  onCancel: (r: OwnRequest) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const live = r.status === "proposed" && !r.lapsed;
  return (
    <div className="border-t border-line px-3.5 py-2.5">
      <p className="text-[12.5px] leading-snug text-ink">
        <span className="font-medium">
          Requested {Math.round(r.qty).toLocaleString("en-IN")} {unit} from {r.from_name}
        </span>
        <span className="text-ink-2">
          {" "}· {live ? `awaiting reply · sent ${waited(r.created_at)}` : r.words}
        </span>
      </p>
      {live ? (
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <span className="text-[11px] text-ink-3">
            Lapses at {clock(r.lapses_at)} if {r.from_name} does not reply.
          </span>
          <button
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await onCancel(r);
              } catch (e) {
                setErr(e instanceof ApiError ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            className="min-h-9 shrink-0 rounded-md border border-line px-2.5 text-[12px] font-medium text-ink-2 hover:border-crit hover:text-crit disabled:opacity-50"
          >
            {busy ? "Cancelling…" : "Cancel request"}
          </button>
        </div>
      ) : (
        r.lapsed && (
          <p className="mt-0.5 text-[11px] text-ink-3">
            No reply by {clock(r.lapses_at)}. You can ask another centre.
          </p>
        )
      )}
      {err && <p role="alert" className="mt-1 text-[11.5px] text-crit">{err}</p>}
    </div>
  );
}

/** Fix #84: the last 28 days' use and the forecast, loaded only when opened. */
function UseAndForecast({ facilityId, sku }: { facilityId: string; sku: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="px-3.5 py-2.5">
      <details onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary className="cursor-pointer text-[12px] text-ink-2">{both("useAndForecast")}</summary>
        {open && <UsageChart facilityId={facilityId} sku={sku} />}
      </details>
    </div>
  );
}

function MedicineCard({
  facilityId,
  s,
  request,
  onFindSupply,
  onCancel,
}: {
  facilityId: string;
  s: WorkspaceSku;
  /** This centre's latest request for the medicine in the last day, if any. */
  request: OwnRequest | undefined;
  onFindSupply: (s: WorkspaceSku) => void;
  onCancel: (r: OwnRequest) => Promise<void>;
}) {
  const waiting = !!request && request.status === "proposed" && !request.lapsed;
  const style = STATUS_STYLE[s.status] ?? STATUS_STYLE.healthy;
  const short = s.status === "critical" || s.status === "at_risk";
  // The rule that produced days-of-cover changes what the date means, so it is
  // named rather than left implied. rate_source is already on the wire.
  const rule =
    s.rate_source === "federated"
      ? `from the shared model's forecast${
          s.forecast_published_at ? `, published ${longDate(s.forecast_published_at)}` : ""
        }`
      : "from the last 28 days of readings";

  return (
    <article className="rounded-lg border border-line bg-panel">
      <div className="flex items-start gap-3 border-b border-line px-3.5 py-3">
        <span
          aria-hidden="true"
          className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${style.dot}`}
        />
        <div className="min-w-0 flex-1">
          <h3 className="text-[14.5px] leading-snug font-semibold text-ink">{s.sku_name}</h3>
          <p className={`mt-0.5 text-[11.5px] font-medium ${style.text}`}>{style.label}</p>
        </div>
        <div className="shrink-0 text-right">
          {/* Whole units. A shelf holds 349 capsules, not 349.38 of one — the
              stored float is a burn-rate artefact, and printing it invites a
              pharmacist to wonder which of the two numbers is wrong. */}
          <div className="font-mono text-[17px] leading-none font-semibold text-ink">
            {Math.round(s.qty_on_hand).toLocaleString("en-IN")}
          </div>
          <div className="mt-1 text-[11px] text-ink-3">{s.unit}</div>
        </div>
      </div>

      <dl className="divide-y divide-line">
        <div className="flex items-baseline justify-between gap-3 px-3.5 py-2.5">
          <dt className="text-[12px] text-ink-2">{both("daysOfCover")}</dt>
          <dd className="text-right text-[12.5px] font-medium text-ink">
            {s.count_overdue ? both("countOverdue") : formatDays(s.days_of_stock)}
          </dd>
        </div>

        <div className="px-3.5 py-2.5">
          <dt className="text-[12px] text-ink-2">{both("runsOut")}</dt>
          <dd className="mt-0.5 text-[12.5px] text-ink">
            {s.count_overdue && s.stockout_on && s.last_reported_at ? (
              // As of now, not as of the count (fix #26).
              <>
                Last counted {Math.round(s.qty_on_hand).toLocaleString("en-IN")} {s.unit} on{" "}
                {longDate(s.last_reported_at)}. At your usual use it would have run out around{" "}
                <span className="font-medium">{longDate(s.stockout_on)}</span>.{" "}
                <span className="font-medium">{both("countTheShelf")}.</span>
              </>
            ) : s.stockout_on ? (
              <>
                <span className="font-medium">{longDate(s.stockout_on)}</span>
                <span className="text-ink-3"> — {rule}</span>
              </>
            ) : (
              // Review Focus 1: no burn rate, so no date. Saying so is the
              // honest answer; a guessed date would be worse than silence.
              <span className="text-ink-3">{both("notEnoughReadings")}</span>
            )}
          </dd>
        </div>

        <UseAndForecast facilityId={facilityId} sku={s.sku_code} />

        <div className="px-3.5 py-2.5">
          <dt className="text-[12px] text-ink-2">How this was checked</dt>
          <dd className="mt-0.5 text-[12.5px] text-ink">
            <span className="font-medium">{PROVENANCE_LABEL[s.provenance.kind]}</span>
            {s.provenance.days_ago !== null && (
              <span className="text-ink-2">, {ago(s.provenance.days_ago)}</span>
            )}
            <span className="mt-0.5 block text-[11.5px] leading-snug text-ink-3">
              {s.provenance.detail}
            </span>
          </dd>
        </div>
      </dl>

      {request && <RequestChip r={request} unit={s.unit} onCancel={onCancel} />}
      {short && !waiting && (
        <div className="border-t border-line px-3.5 py-2.5">
          <button
            onClick={() => onFindSupply(s)}
            className="min-h-11 w-full rounded-md bg-brand px-3 text-[13px] font-medium text-white"
          >
            {both("findSupply")}
          </button>
        </div>
      )}
    </article>
  );
}

export default function Medicines({
  facilityId,
  refreshKey,
  onChanged,
  onOpenField,
}: {
  facilityId: string;
  refreshKey: number;
  onChanged: () => void;
  /** Opens the field simulator in place — no navigation, no reload. */
  onOpenField: () => void;
}) {
  const [view, setView] = useState<WorkspaceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [supplyFor, setSupplyFor] = useState<WorkspaceSku | null>(null);
  const [supply, setSupply] = useState<Supply | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .workspace(facilityId)
      .then((v) => alive && (setView(v), setError(null)))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [facilityId, refreshKey]);

  const openSupply = useCallback(
    (s: WorkspaceSku) => {
      setSupplyFor(s);
      setSupply(null);
      api
        .supply(facilityId, s.sku_code)
        .then(setSupply)
        .catch((e) => setError(String(e)));
    },
    [facilityId],
  );

  if (error) {
    return (
      <p role="alert" className="text-[12.5px] text-crit">
        {error}
      </p>
    );
  }
  if (!view) {
    return <p className="text-[12.5px] text-ink-3">Loading medicines…</p>;
  }

  const trust = view.facility.trust_score;
  const band = view.facility.trust_band ? TRUST_BAND[view.facility.trust_band] : null;
  // Newest first from the server, so the first match is the latest request.
  const requestFor = (code: string) => view.requests.find((r) => r.sku_code === code);
  // Errors stay on the chip that raised them (RequestChip); success reloads.
  const cancel = async (r: OwnRequest) => {
    await api.cancelRequest(r.transfer_id);
    onChanged();
  };

  return (
    <>
      {/* Fix #58: shown only when an outbreak is active in this district —
          the same rows the officer's panel reads, never a separate copy. */}
      {view.outbreaks.map((o) => (
        <section
          key={o.outbreak_id}
          role="alert"
          className="mb-3 rounded-lg border border-risk/40 bg-risk/5 px-3.5 py-3"
        >
          <h2 className="text-[13px] font-semibold text-ink">{o.headline}</h2>
          <p className="mt-0.5 text-[11.5px] text-ink-2">
            प्रकोप की सूचना · Expected use is raised for these medicines until the outbreak ends.
          </p>
          <ul className="mt-1.5 space-y-1">
            {o.medicines.map((m) => {
              const s = view.skus.find((x) => x.sku_code === m.sku_code);
              return (
                <li key={m.sku_code} className="flex items-baseline justify-between gap-2 text-[12px]">
                  <span className="text-ink">
                    <span className="font-medium">{m.sku_name}</span>
                    {m.multiplier !== null ? (
                      <span className="text-ink-2">
                        {" "}
                        · {formatDays(m.days_at_outbreak_rate)} at the outbreak rate ({formatDays(m.days_now)} usually
                        {m.basis === "assumption" ? "; the rise is the officer's assumption" : "; rise observed in this district"})
                      </span>
                    ) : (
                      <span className="text-ink-3"> · no rise expected yet</span>
                    )}
                  </span>
                  {s && m.multiplier !== null && (
                    <button onClick={() => openSupply(s)} className="shrink-0 font-medium text-brand hover:underline">
                      {both("findSupply")}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      <Briefing facilityId={facilityId} computed={view.briefing} />

      <StockPhoto facilityId={facilityId} onCommitted={onChanged} />

      {/* The channels this centre's staff can use when they are not at a
          screen. Nine in ten sub-centres have no reliable data connection, so
          the phone paths are not a fallback — for many facilities they are the
          only path. Said here, on the pharmacist's own screen, because that is
          where somebody wonders how a colleague in the field reports. */}
      <section
        aria-label="Reporting without this app"
        className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
      >
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
          Away from a screen
        </h2>
        <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">
          Staff can report stock, check in, and confirm a delivery by{" "}
          <span className="font-medium text-ink">SMS, WhatsApp or a phone call</span> —
          no app and no data connection. A text reading{" "}
          <span className="font-mono text-[11.5px] text-ink">ORS 60</span> updates
          this same shelf.
        </p>
        {/* Opens inside this workspace (fix list #24). It was a link to the
            officer console's field view: a full page load that landed on the
            front door, for a view a pharmacist's account never reaches. */}
        <button
          type="button"
          onClick={onOpenField}
          className="mt-2 min-h-11 text-left text-[12.5px] font-medium text-brand underline"
        >
          {both("openFieldSimulator")}
        </button>
      </section>

      <Team facilityId={facilityId} refreshKey={refreshKey} />

      <section
        aria-label="Data confidence"
        className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
      >
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-[12px] font-medium text-ink-2">{both("dataConfidence")}</h2>
          {/* Same score, scale and band words as the officer's drawer (fix #89). */}
          <span className="flex items-baseline gap-1.5">
            <span className="font-mono text-[14px] font-semibold text-ink">
              {trust === null ? "—" : Math.round(trust * 100)}
            </span>
            {trust !== null && <span className="text-[11.5px] text-ink-2">out of 100</span>}
            {band && (
              <span
                className={`ml-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${band.className}`}
              >
                {band.label}
              </span>
            )}
          </span>
        </div>
        <p className="mt-1 text-[11.5px] leading-snug text-ink-3">
          {band
            ? "Computed live from this centre's own reports — reporting regularly and consistently raises it."
            : "Not enough history at this centre to compute a confidence score yet."}
        </p>
        <p className="mt-1.5 text-[11.5px] text-ink-3">
          {view.open_requests} of {view.max_open_requests} stock requests waiting for a reply
        </p>
      </section>

      <ul className="flex flex-col gap-3">
        {view.skus.map((s) => (
          <li key={s.sku_code}>
            <MedicineCard
              facilityId={facilityId}
              s={s}
              request={requestFor(s.sku_code)}
              onFindSupply={openSupply}
              onCancel={cancel}
            />
          </li>
        ))}
      </ul>

      {supplyFor && (
        <FindSupply
          facility={view.facility}
          sku={supplyFor}
          supply={supply}
          onClose={() => {
            setSupplyFor(null);
            setSupply(null);
          }}
          onRequested={() => {
            setSupplyFor(null);
            setSupply(null);
            onChanged();
          }}
        />
      )}
    </>
  );
}

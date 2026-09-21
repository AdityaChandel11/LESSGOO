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
  type ProvenanceKind,
  type Supply,
  type WorkspaceSku,
  type WorkspaceView,
  api,
  formatDays,
} from "../api";
import FindSupply from "./FindSupply";
import { both } from "./labels";

const STATUS_STYLE: Record<string, { dot: string; text: string; label: string }> = {
  critical: { dot: "bg-crit", text: "text-crit", label: "Critical" },
  at_risk: { dot: "bg-risk", text: "text-risk", label: "At risk" },
  healthy: { dot: "bg-ok", text: "text-ok", label: "Healthy" },
};

const PROVENANCE_LABEL: Record<ProvenanceKind, string> = {
  counted: "Counted by hand",
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

function MedicineCard({
  s,
  onFindSupply,
}: {
  s: WorkspaceSku;
  onFindSupply: (s: WorkspaceSku) => void;
}) {
  const style = STATUS_STYLE[s.status] ?? STATUS_STYLE.healthy;
  const short = s.status === "critical" || s.status === "at_risk";
  // The rule that produced days-of-cover changes what the date means, so it is
  // named rather than left implied. rate_source is already on the wire.
  const rule =
    s.rate_source === "federated"
      ? "from the shared model's forecast"
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
            {formatDays(s.days_of_stock)}
          </dd>
        </div>

        <div className="px-3.5 py-2.5">
          <dt className="text-[12px] text-ink-2">{both("runsOut")}</dt>
          <dd className="mt-0.5 text-[12.5px] text-ink">
            {s.stockout_on ? (
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

      {short && (
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
}: {
  facilityId: string;
  refreshKey: number;
  onChanged: () => void;
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

  return (
    <>
      <section
        aria-label="Data confidence"
        className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
      >
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-[12px] font-medium text-ink-2">{both("dataConfidence")}</h2>
          <span className="font-mono text-[14px] font-semibold text-ink">
            {trust === null ? "—" : trust.toFixed(2)}
          </span>
        </div>
        <p className="mt-1 text-[11.5px] leading-snug text-ink-3">
          {view.facility.trust_band
            ? `Band: ${view.facility.trust_band}. Computed live from this centre's own reports — reporting regularly and consistently raises it.`
            : "Not enough history at this centre to compute a confidence score yet."}
        </p>
        <p className="mt-1.5 text-[11.5px] text-ink-3">
          {view.open_requests} of {view.max_open_requests} stock requests open
        </p>
      </section>

      <ul className="flex flex-col gap-3">
        {view.skus.map((s) => (
          <li key={s.sku_code}>
            <MedicineCard s={s} onFindSupply={openSupply} />
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

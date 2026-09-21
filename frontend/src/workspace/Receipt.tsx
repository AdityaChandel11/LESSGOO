/**
 * The receipt — the piece of paper that makes the request real in a room with
 * no computer in it.
 *
 * A reference number, both parties, the medicine and quantity, and the
 * timestamps that actually exist on the row. Nothing on this page is invented:
 * the estimate is labelled an estimate, its five assumptions are printed
 * underneath it in words, and the distance says it is a straight line.
 *
 * Printing is the browser's job. There is no PDF library here and there does
 * not need to be one: `window.print()` against a page that already has a print
 * stylesheet produces something a district office can file.
 */

import { type FacilityDetail, type StockRequest } from "../api";
import DataNotice from "../DataNotice";
import { both } from "./labels";

const ROLE_WORDS: Record<string, string> = {
  block_mo: "District logistics officer",
  state_officer: "State NHM officer",
  admin: "Platform administrator",
};

/** The server returns the constants it used; this turns each into a sentence. */
const ASSUMPTION_WORDS: Record<string, (v: number) => string> = {
  avg_speed_kmh: (v) => `Vehicles average ${v} km/h on district roads`,
  handling_hours: (v) => `Loading and paperwork take ${v} hours`,
  road_factor: (v) => `Road distance is taken as ${v}× the straight line`,
  dispatch_cutoff_hour: (v) =>
    `Requests raised after ${String(v).padStart(2, "0")}:00 leave the next morning`,
  working_hours_per_day: (v) => `A vehicle runs ${v} hours in a day`,
};

function longDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-line py-2">
      <dt className="shrink-0 text-[12px] text-ink-2">{label}</dt>
      <dd className="text-right text-[12.5px] font-medium text-ink">{children}</dd>
    </div>
  );
}

export default function Receipt({
  request,
  facility,
  onClose,
}: {
  request: StockRequest;
  facility: FacilityDetail;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[1400] flex flex-col overflow-y-auto bg-panel">
      <header className="no-print flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
        <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-ink">
          Request sent
        </h2>
        <button
          onClick={() => window.print()}
          className="min-h-11 rounded-md border border-line px-3 text-[13px] font-medium text-ink"
        >
          {both("print")}
        </button>
        <button onClick={onClose} className="min-h-11 px-2 text-[13px] font-medium text-brand">
          Done
        </button>
      </header>

      <main className="px-4 py-5">
        <p className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
          Stock request
        </p>
        <h1 className="mt-1 font-mono text-[22px] leading-none font-semibold tracking-tight text-ink">
          {request.reference}
        </h1>
        <p className="mt-2 text-[12.5px] leading-relaxed text-ink-2">
          This request is awaiting approval. No stock has moved.
        </p>

        <dl className="mt-5">
          <Row label="Medicine">
            {request.sku_name}
            <span className="block text-[11px] font-normal text-ink-3">
              {request.sku_code}
            </span>
          </Row>
          <Row label="Quantity requested">
            {Math.round(request.qty).toLocaleString("en-IN")} {request.unit}
          </Row>
          <Row label="From">
            {request.from_name}
            <span className="block text-[11px] font-normal text-ink-3">
              {request.from_facility}
            </span>
          </Row>
          <Row label="To">
            {facility.name}
            <span className="block text-[11px] font-normal text-ink-3">
              {facility.district}, {facility.state_silo}
            </span>
          </Row>
          <Row label="Status">{request.status}</Row>
          <Row label="Awaiting approval from">
            {ROLE_WORDS[request.approver_role] ?? request.approver_role}
          </Row>
          <Row label="Distance">
            {request.km} km
            <span className="block text-[11px] font-normal text-ink-3">
              straight-line estimate, not a road route
            </span>
          </Row>
          <Row label="Estimated delivery">
            {longDate(request.estimated_delivery)}
            <span className="block text-[11px] font-normal text-ink-3">
              {request.estimate_label} — not a scheduled time
            </span>
          </Row>
        </dl>

        <section aria-label="Estimate assumptions" className="mt-5">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
            What that estimate assumes
          </h2>
          <ul className="mt-1.5 flex flex-col gap-1">
            {Object.entries(request.assumptions).map(([k, v]) => (
              <li key={k} className="text-[11.5px] leading-snug text-ink-2">
                · {ASSUMPTION_WORDS[k] ? ASSUMPTION_WORDS[k](v) : `${k}: ${v}`}
              </li>
            ))}
          </ul>
        </section>

        <div className="mt-6 border-t border-line pt-3">
          <DataNotice />
        </div>
      </main>
    </div>
  );
}

/**
 * Asking for stock — and being honest that asking is all this does.
 *
 * Spec 12.3: redistribution always ends in human approval, and nothing
 * auto-executes. So the button is careful about what it promises. It says, in
 * the sentence above it, that no stock moves until an officer approves; and
 * afterwards it names the officer whose approval is actually required, which
 * depends on whether the donor is inside this district or outside it.
 *
 * The delivery estimate is deliberately not shown here: it does not exist
 * until the server computes it from the real distance, and a form that
 * previewed one would be showing a number nothing had calculated. It appears
 * on the receipt instead, with the five assumptions the server returned
 * alongside it.
 */

import { type FormEvent, useState } from "react";

import { ApiError, type Donor, type FacilityDetail, type StockRequest, type Supply, api } from "../api";
import Receipt from "./Receipt";
import { both } from "./labels";

const ROLE_WORDS: Record<string, string> = {
  block_mo: "the district logistics officer",
  state_officer: "the state NHM officer",
  admin: "a platform administrator",
};


export default function RequestStock({
  facility,
  donor,
  supply,
  onCancel,
  onDone,
}: {
  facility: FacilityDetail;
  donor: Donor;
  supply: Supply;
  onCancel: () => void;
  onDone: () => void;
}) {
  // Pre-filled with what the centre actually needs, ceilinged at what this
  // donor can give without breaching its own floor.
  const ceiling = donor.spare_units;
  // The same rule auth.can_decide_transfer applies: a block_mo may only decide
  // inside its own district, so a donor from elsewhere needs the state officer.
  const approverRole =
    donor.district === facility.district ? "block_mo" : "state_officer";
  const [qty, setQty] = useState(String(Math.min(supply.units_needed, ceiling)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<StockRequest | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const n = Number(qty);
    if (!Number.isFinite(n) || n <= 0) {
      setError("Enter how many units you need.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setDone(
        await api.requestStock(facility.id, {
          sku_code: supply.sku_code,
          from_facility: donor.facility_id,
          qty: n,
        }),
      );
    } catch (err) {
      // The server's refusals are written to be read by the person who hit
      // them — the donor's floor, the cap, the controlled-substance rule — so
      // they are shown as-is rather than replaced with a generic message.
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return <Receipt request={done} facility={facility} onClose={onDone} />;
  }

  return (
    <div className="fixed inset-0 z-[1300] flex flex-col bg-canvas">
      <header className="flex shrink-0 items-start gap-3 border-b border-line bg-panel px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
            {both("requestStock")}
          </p>
          <h2 className="mt-0.5 truncate text-[16px] font-semibold text-ink">
            {supply.sku_name}
          </h2>
          <p className="mt-0.5 truncate text-[12px] text-ink-2">from {donor.name}</p>
        </div>
        <button
          onClick={onCancel}
          className="-mr-1 min-h-11 shrink-0 px-2 text-[13px] font-medium text-brand"
        >
          Cancel
        </button>
      </header>

      <form onSubmit={submit} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <label htmlFor="qty" className="block text-[12.5px] font-medium text-ink">
          How many {supply.unit}?
        </label>
        <input
          id="qty"
          type="number"
          inputMode="numeric"
          min={1}
          max={ceiling}
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          className="mt-1.5 h-12 w-full rounded-md border border-line bg-panel px-3 font-mono text-[15px] text-ink focus:border-brand focus:outline-none"
        />
        <p className="mt-1.5 text-[11.5px] leading-snug text-ink-3">
          {donor.name} can spare up to {ceiling.toLocaleString("en-IN")} {supply.unit} and
          still keep {donor.days_kept.toFixed(1)} days of its own cover.
        </p>

        {error && (
          <p
            role="alert"
            className="mt-3 rounded-md border border-crit/25 bg-crit/5 px-3 py-2.5 text-[12.5px] leading-relaxed text-crit"
          >
            {error}
          </p>
        )}

        <p className="mt-4 rounded-md border border-line bg-panel px-3 py-2.5 text-[12.5px] leading-relaxed text-ink-2">
          This asks {ROLE_WORDS[approverRole]} to approve the transfer.{" "}
          <span className="font-medium text-ink">No stock moves until they do.</span>
        </p>

        <button
          type="submit"
          disabled={busy}
          className="mt-4 min-h-12 w-full rounded-md bg-brand px-3 text-[14px] font-medium text-white disabled:opacity-60"
        >
          {busy ? "Sending…" : "Send request"}
        </button>
      </form>
    </div>
  );
}

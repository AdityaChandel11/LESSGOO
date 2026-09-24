/**
 * What is on its way, and confirming it arrived.
 *
 * No new backend: this is the existing movements ledger filtered to one
 * facility, and the existing receipt endpoint. Both already carry the right
 * permission — confirming a delivery is reporting a fact about your own
 * centre, so it needs the same scope as a stock reading.
 *
 * The confirmation asks for the quantity actually received rather than
 * offering a single "confirm" button, because the two-sided ledger (spec 26.3)
 * only means something if the receiving side can disagree with the dispatch.
 * A short delivery is reported as short and stays visible as a discrepancy;
 * it is not quietly rounded up to what was sent.
 */

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, type Movement, api } from "../api";
import Incoming from "./Incoming";
import { both } from "./labels";

const STATUS_WORDS: Record<string, { label: string; className: string }> = {
  in_transit: { label: "In transit", className: "text-ink-2" },
  overdue: { label: "Overdue", className: "text-risk" },
  received: { label: "Received", className: "text-ok" },
  short: { label: "Short delivery", className: "text-crit" },
  over: { label: "Over delivery", className: "text-risk" },
  cancelled: { label: "Cancelled", className: "text-ink-3" },
};

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function ConfirmForm({
  m,
  onConfirmed,
}: {
  m: Movement;
  onConfirmed: () => void;
}) {
  const [qty, setQty] = useState(String(m.qty_dispatched));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const n = Number(qty);
    if (!Number.isFinite(n) || n < 0) {
      setError("Enter the number of units that actually arrived.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.confirmReceipt(m.id, n, note || undefined);
      onConfirmed();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-3 border-t border-line pt-3">
      <label
        htmlFor={`qty-${m.id}`}
        className="block text-[12px] font-medium text-ink"
      >
        Quantity received
      </label>
      <input
        id={`qty-${m.id}`}
        type="number"
        inputMode="numeric"
        min={0}
        value={qty}
        onChange={(e) => setQty(e.target.value)}
        className="mt-1.5 h-12 w-full rounded-md border border-line bg-panel px-3 font-mono text-[15px] text-ink focus:border-brand focus:outline-none"
      />
      <p className="mt-1.5 text-[11.5px] leading-snug text-ink-3">
        Count what arrived. If it is less than the {m.qty_dispatched.toLocaleString("en-IN")}{" "}
        {m.unit} dispatched, enter the smaller number — the difference is recorded as a
        discrepancy for the district to follow up.
      </p>

      <label htmlFor={`note-${m.id}`} className="mt-3 block text-[12px] font-medium text-ink">
        Note <span className="font-normal text-ink-3">(optional)</span>
      </label>
      <input
        id={`note-${m.id}`}
        type="text"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className="mt-1.5 h-11 w-full rounded-md border border-line bg-panel px-3 text-[13px] text-ink focus:border-brand focus:outline-none"
      />

      {error && (
        <p role="alert" className="mt-2 text-[12.5px] leading-relaxed text-crit">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="mt-3 min-h-12 w-full rounded-md bg-brand px-3 text-[13.5px] font-medium text-white disabled:opacity-60"
      >
        {busy ? "Confirming…" : "Confirm"}
      </button>
    </form>
  );
}

function MovementCard({ m, onChanged }: { m: Movement; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const words = STATUS_WORDS[m.status] ?? STATUS_WORDS.in_transit;
  const settled = m.qty_received != null;

  return (
    <article className="rounded-lg border border-line bg-panel px-3.5 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] leading-snug font-semibold text-ink">{m.sku_name}</h3>
          <p className="mt-0.5 font-mono text-[11px] text-ink-3">{m.batch_id}</p>
        </div>
        <span className={`shrink-0 text-[11.5px] font-medium ${words.className}`}>
          {words.label}
        </span>
      </div>

      <dl className="mt-2 flex flex-col gap-1 text-[12.5px]">
        <div className="flex justify-between gap-3">
          <dt className="text-ink-2">Dispatched</dt>
          <dd className="text-right font-medium text-ink">
            {m.qty_dispatched.toLocaleString("en-IN")} {m.unit} · {shortDate(m.dispatched_at)}
          </dd>
        </div>
        {settled ? (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-2">Received</dt>
            <dd className="text-right font-medium text-ink">
              {m.qty_received!.toLocaleString("en-IN")} {m.unit} ·{" "}
              {shortDate(m.received_at!)}
            </dd>
          </div>
        ) : (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-2">Expected by</dt>
            <dd className="text-right font-medium text-ink">{shortDate(m.expected_by)}</dd>
          </div>
        )}
        {m.discrepancy_qty != null && m.discrepancy_qty !== 0 && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-2">Difference</dt>
            <dd className="text-right font-medium text-crit">
              {m.discrepancy_qty > 0 ? "+" : ""}
              {m.discrepancy_qty.toLocaleString("en-IN")} {m.unit}
            </dd>
          </div>
        )}
      </dl>

      {!settled && !open && (
        <button
          onClick={() => setOpen(true)}
          className="mt-3 min-h-11 w-full rounded-md border border-brand px-3 text-[13px] font-medium text-brand"
        >
          {both("confirmReceipt")}
        </button>
      )}
      {!settled && open && <ConfirmForm m={m} onConfirmed={onChanged} />}
    </article>
  );
}

function OrdersList({
  facilityId,
  refreshKey,
  onChanged,
}: {
  facilityId: string;
  refreshKey: number;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<Movement[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .movements({ view: "all", facility: facilityId, limit: 50 })
      .then((r) => (setRows(r.movements), setError(null)))
      .catch((e) => setError(String(e)));
  }, [facilityId]);

  useEffect(load, [load, refreshKey]);

  if (error) {
    return (
      <p role="alert" className="text-[12.5px] text-crit">
        {error}
      </p>
    );
  }
  if (!rows) return <p className="text-[12.5px] text-ink-3">Loading orders…</p>;
  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-line bg-panel px-3.5 py-4 text-[12.5px] leading-relaxed text-ink-2">
        Nothing is on its way to this centre. Requests you raise from the Medicines tab
        appear here once the donor centre accepts them.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-3">
      {rows.map((m) => (
        <li key={m.id}>
          <MovementCard
            m={m}
            onChanged={() => {
              load();
              onChanged();
            }}
          />
        </li>
      ))}
    </ul>
  );
}

export default function Orders(props: {
  facilityId: string;
  refreshKey: number;
  onChanged: () => void;
  demoMode?: boolean;
}) {
  return (
    <div>
      <Incoming facilityId={props.facilityId} demoMode={!!props.demoMode} onChanged={props.onChanged} />
      <h2 className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">On its way to you</h2>
      <OrdersList {...props} />
    </div>
  );
}

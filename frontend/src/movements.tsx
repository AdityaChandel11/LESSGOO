/**
 * Medicine movement ledger — spec 26.3.
 *
 * Every row is one batch with two independent halves: what the warehouse says
 * it sent, and what the facility confirms it received. The panel opens on the
 * exceptions rather than on history, because a ledger only earns its place if
 * the problems find the officer instead of the officer finding them.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  MOVEMENT_LABEL,
  MOVEMENT_TONE,
  api,
  type Movement,
  type MovementView,
  type Movements,
  type User,
  can,
} from "./api";

const VIEWS: { key: MovementView; label: string; countKey: string }[] = [
  { key: "attention", label: "Needs attention", countKey: "attention" },
  { key: "in_transit", label: "In transit", countKey: "in_transit" },
  { key: "received", label: "Received", countKey: "received" },
  { key: "all", label: "All", countKey: "total" },
];

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function daysAgo(iso: string): string {
  const days = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  if (days < 1) return "today";
  if (days < 2) return "yesterday";
  return `${Math.floor(days)} days ago`;
}

/** The sentence under a row: what is actually wrong, in words. */
function explain(m: Movement): string | null {
  if (m.status === "overdue") {
    const late = m.days_outstanding ?? 0;
    return `Dispatched ${daysAgo(m.dispatched_at)} from ${m.from_ref}. No confirmation ${
      late < 1 ? "yet" : `for ${late.toFixed(0)} day${late < 2 ? "" : "s"} past the delivery window`
    }.`;
  }
  if (m.status === "short" && m.discrepancy_qty != null) {
    const gap = Math.abs(m.discrepancy_qty);
    const pct = (gap / m.qty_dispatched) * 100;
    return `${gap.toLocaleString("en-IN")} ${m.unit} short of the dispatched quantity (${pct.toFixed(0)}%).`;
  }
  if (m.status === "over" && m.discrepancy_qty != null) {
    return `${m.discrepancy_qty.toLocaleString("en-IN")} ${m.unit} more than dispatched — usually a recording error at one end.`;
  }
  return null;
}

function StatusChip({ status }: { status: Movement["status"] }) {
  return (
    <span
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${MOVEMENT_TONE[status]}`}
    >
      {MOVEMENT_LABEL[status]}
    </span>
  );
}

function ReceiptForm({
  movement,
  onConfirm,
}: {
  movement: Movement;
  onConfirm: (qty: number, note: string) => Promise<void>;
}) {
  const [qty, setQty] = useState(String(movement.qty_dispatched));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = Number(qty);
  const valid = qty.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
  const gap = valid ? parsed - movement.qty_dispatched : 0;

  return (
    <form
      className="mt-2 rounded-md border border-line bg-canvas p-2.5"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!valid || busy) return;
        setBusy(true);
        setError(null);
        try {
          await onConfirm(parsed, note.trim());
        } catch (err) {
          setError(err instanceof ApiError ? err.message : "Could not confirm this delivery");
          setBusy(false);
        }
      }}
    >
      <label className="block text-[11px] font-medium text-ink-2" htmlFor={`qty-${movement.id}`}>
        Quantity actually received ({movement.unit})
      </label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id={`qty-${movement.id}`}
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          inputMode="decimal"
          className="w-28 rounded border border-line bg-panel px-2 py-1 font-mono text-[12.5px] text-ink"
        />
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional)"
          className="min-w-0 flex-1 rounded border border-line bg-panel px-2 py-1 text-[12.5px] text-ink placeholder:text-ink-3"
        />
        <button
          type="submit"
          disabled={!valid || busy}
          className="shrink-0 rounded bg-brand px-3 py-1 text-[12.5px] font-medium text-white disabled:opacity-50"
        >
          {busy ? "Confirming…" : "Confirm receipt"}
        </button>
      </div>
      {valid && gap !== 0 && (
        <p className="mt-1.5 text-[11.5px] text-ink-2">
          {gap < 0
            ? `Recorded as short by ${Math.abs(gap).toLocaleString("en-IN")} ${movement.unit}. The gap stays on the ledger for the warehouse to answer.`
            : `Recorded as over by ${gap.toLocaleString("en-IN")} ${movement.unit} — worth re-counting before confirming.`}
        </p>
      )}
      {error && <p className="mt-1.5 text-[11.5px] text-crit">{error}</p>}
    </form>
  );
}

function MovementRow({
  m,
  canConfirm,
  onConfirm,
}: {
  m: Movement;
  canConfirm: boolean;
  onConfirm: (qty: number, note: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const reason = explain(m);
  const settled = m.qty_received != null;

  return (
    <div className="border-b border-line px-3 py-2.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-[13px] font-medium text-ink">{m.sku_name}</span>
            <span className="shrink-0 font-mono text-[11px] text-ink-3">{m.batch_id}</span>
          </div>
          <p className="mt-0.5 truncate text-[12px] text-ink-2">
            {m.from_ref} → {m.facility_name}
            <span className="text-ink-3"> · {m.district}</span>
          </p>
        </div>
        <StatusChip status={m.status} />
      </div>

      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11.5px] text-ink-2">
        <span>
          sent {m.qty_dispatched.toLocaleString("en-IN")} {m.unit} · {shortDate(m.dispatched_at)}
        </span>
        {settled ? (
          <span>
            received {m.qty_received!.toLocaleString("en-IN")} · {shortDate(m.received_at!)}
            {m.received_via && <span className="text-ink-3"> · {m.received_via}</span>}
          </span>
        ) : (
          <span className="text-ink-3">due {shortDate(m.expected_by)}</span>
        )}
      </div>

      {reason && <p className="mt-1 text-[12px] text-ink-2">{reason}</p>}
      {m.note && <p className="mt-0.5 text-[11.5px] italic text-ink-3">“{m.note}”</p>}

      {!settled &&
        (canConfirm ? (
          open ? (
            <ReceiptForm movement={m} onConfirm={onConfirm} />
          ) : (
            <button
              onClick={() => setOpen(true)}
              className="mt-2 rounded border border-line px-2.5 py-1 text-[12px] font-medium text-ink-2 hover:border-brand hover:text-ink"
            >
              Confirm what arrived
            </button>
          )
        ) : (
          <p className="mt-1.5 text-[11.5px] text-ink-3">
            Waiting on {m.facility_name} to confirm what arrived.
          </p>
        ))}
    </div>
  );
}

export function MovementsPanel({
  stateLabel,
  state,
  sku,
  facility,
  initialView,
  onClearFacility,
  user,
  refreshKey,
  onReceipt,
}: {
  stateLabel: string;
  state: string | null;
  sku: string | null;
  /** Set when the tab was opened from a trust score's evidence link: the same
   *  filter the score itself was computed with. */
  facility: { id: string; name: string } | null;
  initialView?: MovementView;
  onClearFacility: () => void;
  user: User;
  /** Bumped by the live feed so a delivery confirmed elsewhere shows up here. */
  refreshKey: number;
  onReceipt: (result: { facilityName: string; statusChanged: boolean; statusAfter: string }) => void;
}) {
  const [view, setView] = useState<MovementView>(initialView ?? "attention");
  const [data, setData] = useState<Movements | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Re-runs whenever the live feed bumps refreshKey, so a delivery confirmed
  // anywhere — here, by SMS, or by another officer — shows up without a
  // refresh mechanism of its own.
  const load = useCallback(() => {
    setLoading(true);
    api
      .movements({ state, view, sku, facility: facility?.id ?? null })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the ledger"))
      .finally(() => setLoading(false));
  }, [state, view, sku, facility]);

  useEffect(load, [load, refreshKey]);

  const counts = data?.counts ?? {};
  const rows = data?.movements ?? [];

  const confirm = useMemo(
    () => (m: Movement) => async (qty: number, note: string) => {
      const result = await api.confirmReceipt(m.id, qty, note || undefined);
      onReceipt({
        facilityName: m.facility_name,
        statusChanged: result.status_changed,
        statusAfter: result.status_after,
      });
      load();
    },
    [load, onReceipt],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <h2 className="text-[13px] font-semibold text-ink">Medicine movements — {stateLabel}</h2>
        <p className="mt-0.5 text-[12px] text-ink-2">
          Every batch has two records: what the warehouse dispatched, and what the facility
          confirms arrived. Gaps between them show up here without anyone auditing.
        </p>
        {facility && (
          <div className="mt-1.5 flex items-center gap-2 rounded-md border border-line bg-canvas px-2 py-1.5">
            <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">
              Showing the rows behind {facility.name}'s confidence score
            </span>
            <button
              onClick={onClearFacility}
              className="shrink-0 text-[11.5px] font-medium text-brand hover:underline"
            >
              Show all
            </button>
          </div>
        )}
        {(counts.short ?? 0) > 0 && (data?.short_units ?? 0) > 0 && (
          <p className="mt-1.5 text-[12px] font-medium text-crit">
            {(data?.short_units ?? 0).toLocaleString("en-IN")} units unaccounted for across{" "}
            {counts.short} short deliveries.
          </p>
        )}
      </div>

      <div className="flex items-center gap-1 border-b border-line px-3 pt-2">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            onClick={() => setView(v.key)}
            className={`-mb-px border-b-2 px-2.5 pb-2 text-[12.5px] font-medium ${
              view === v.key
                ? "border-brand text-ink"
                : "border-transparent text-ink-3 hover:text-ink-2"
            }`}
          >
            {v.label}
            <span className="ml-1.5 font-mono text-[11px] text-ink-3">
              {counts[v.countKey] ?? 0}
            </span>
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="p-3 text-[12.5px] text-crit">{error}</p>
        ) : loading && rows.length === 0 ? (
          <p className="p-3 text-[12.5px] text-ink-3">Loading the ledger…</p>
        ) : rows.length === 0 ? (
          <p className="p-3 text-[12.5px] text-ink-2">
            {view === "attention"
              ? "Nothing outstanding — every dispatched batch has been confirmed, in full."
              : "No movements match this view."}
          </p>
        ) : (
          rows.map((m) => (
            <MovementRow
              key={m.id}
              m={m}
              canConfirm={can.report(user, {
                id: m.to_facility,
                state_silo: m.state_silo,
                district: m.district,
              })}
              onConfirm={confirm(m)}
            />
          ))
        )}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { ApiError, type Transfer, api, formatDays } from "../api";

const POLL_MS = 10000;

/**
 * Requests other centres have raised for this centre's stock. This centre's
 * staff accept or decline; accepting dispatches the batch at once, and the
 * receiving centre confirms what arrives. Officers watch on the dashboard.
 */
export default function Incoming({
  facilityId,
  demoMode,
  onChanged,
}: {
  facilityId: string;
  demoMode: boolean;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<Transfer[] | null>(null);
  const [busy, setBusy] = useState<number | "demo" | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    api.incoming(facilityId).then(setRows).catch(() => setRows([]));
  }, [facilityId]);

  useEffect(() => {
    load();
    // A neighbour's request should appear without a reload.
    const t = window.setInterval(() => !document.hidden && load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const decide = async (t: Transfer, decision: "approve" | "reject") => {
    setBusy(t.id);
    setNote(null);
    try {
      await api.decide(t.id, decision);
      setNote(
        decision === "approve"
          ? `Accepted: ${Math.round(t.qty)} ${t.unit} of ${t.sku_name} dispatched to ${t.to.name}. They confirm on arrival.`
          : `Declined the request from ${t.to.name}.`,
      );
      load();
      onChanged();
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "Could not record the decision");
    } finally {
      setBusy(null);
    }
  };

  const simulate = async () => {
    setBusy("demo");
    setNote(null);
    try {
      await api.demoRequest(facilityId);
      load();
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "Could not create the request");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3" aria-label="Requests for your stock">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
          Requests for your stock · आपके स्टॉक के अनुरोध
        </h2>
        {demoMode && (
          <button onClick={simulate} disabled={busy !== null} className="text-[11.5px] font-medium text-brand hover:underline disabled:opacity-50">
            {busy === "demo" ? "Asking…" : "Simulate a request from a neighbour"}
          </button>
        )}
      </div>
      {note && <p className="mt-1.5 text-[11.5px] text-ink-2">{note}</p>}
      {!rows ? (
        <p className="mt-1.5 text-[12px] text-ink-3">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-1.5 text-[12px] text-ink-2">No centre is waiting on you.</p>
      ) : (
        <ul className="mt-1.5 flex flex-col gap-2">
          {rows.map((t) => (
            <li key={t.id} className="rounded-md border border-line px-2.5 py-2">
              <p className="text-[12.5px] text-ink">
                <span className="font-medium">{t.to.name}</span> asks for{" "}
                <span className="font-mono">{Math.round(t.qty).toLocaleString("en-IN")}</span> {t.unit} of{" "}
                <span className="font-medium">{t.sku_name}</span>
              </p>
              <p className="mt-0.5 text-[11px] text-ink-3">
                ~{Math.round(t.route_km)} km
                {t.rationale.recipient_days_before != null &&
                  ` · they have ${formatDays(t.rationale.recipient_days_before)} left`}
                {t.rationale.donor_days_after_plan != null &&
                  ` · you keep ${formatDays(t.rationale.donor_days_after_plan)}`}
              </p>
              <div className="mt-1.5 flex justify-end gap-2">
                <button onClick={() => decide(t, "reject")} disabled={busy !== null} className="h-7 rounded-md border border-line px-2.5 text-[12px] font-medium text-ink-2 hover:bg-canvas disabled:opacity-50">
                  Decline
                </button>
                <button onClick={() => decide(t, "approve")} disabled={busy !== null} className="h-7 rounded-md bg-ok px-2.5 text-[12px] font-medium text-white disabled:opacity-50">
                  {busy === t.id ? "Sending…" : "Accept and send"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

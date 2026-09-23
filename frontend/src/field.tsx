/**
 * The field simulator — a basic handset, on screen.
 *
 * Nine in ten of India's health sub-centres have no reliable data connection.
 * The whole omnichannel design exists so that a pharmacist with a ten-year-old
 * phone and one bar of signal can still report stock, and this panel is how a
 * judge sees that working without anyone handing them a SIM card.
 *
 * It is not a mock. Every message here goes through `/api/ingest/simulate`,
 * which builds the same `RawSubmission` a Twilio webhook builds and runs the
 * same ten-stage spine — dedupe, identify, extract, resolve, validate, score,
 * commit, recompute, emit, confirm. The reply shown is the reply the handset
 * would have received, and the readings listed are rows that now exist. The
 * only thing simulated is the carrier.
 *
 * The real webhooks are deliberately not reachable from here. They refuse
 * anything they cannot prove came from Twilio, which in simulator mode is
 * everything — so this panel goes through the authenticated route instead,
 * and the door onto the public internet stays shut.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, type Handset, type SimulateResult, api } from "./api";

type Channel = "sms" | "whatsapp" | "ivr";

const CHANNELS: { id: Channel; label: string; hint: string }[] = [
  { id: "sms", label: "SMS", hint: "Works on any handset, 2G, no data" },
  { id: "whatsapp", label: "WhatsApp", hint: "Where a smartphone and data exist" },
  { id: "ivr", label: "Voice / IVR", hint: "Spoken, for a worker who cannot type" },
];

/** The grammar, as a field worker would be taught it on a printed card. */
const EXAMPLES: { text: string; what: string }[] = [
  { text: "ORS 60 PARA500 120", what: "Report two medicines at once" },
  { text: "ORS 40", what: "Report one" },
  { text: "IN", what: "Check in for a shift" },
  { text: "BEDS 12", what: "Report bed occupancy (unverified — no photo)" },
  { text: "HELP", what: "Ask what the format is" },
  { text: "zink 30", what: "A misspelling the matcher still resolves" },
];

function Pill({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.06em] ${
        ok ? "bg-ok/10 text-ok" : "bg-crit/10 text-crit"
      }`}
    >
      {children}
    </span>
  );
}

export function FieldSimulator({ facilityId }: { facilityId: string | null }) {
  const [handsets, setHandsets] = useState<Handset[]>([]);
  const [sender, setSender] = useState("");
  const [channel, setChannel] = useState<Channel>("sms");
  const [text, setText] = useState("ORS 60 PARA500 120");
  const [log, setLog] = useState<{ sent: string; result: SimulateResult }[]>([]);
  // The id of the last message sent. Resending it is what a carrier retry
  // actually looks like — two clicks of Send are two different messages, and
  // claiming otherwise would demonstrate nothing.
  const [lastId, setLastId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!facilityId) return;
    api
      .handsets(facilityId)
      .then((h) => {
        setHandsets(h);
        setSender(h[0]?.number ?? "");
      })
      .catch(() => setHandsets([]));
  }, [facilityId]);

  const send = useCallback(async (reuseId?: string) => {
    if (!sender || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const externalId = reuseId ?? `sim:${sender}:${Date.now()}`;
      const result = await api.simulate({
        channel, sender, text, external_id: externalId,
      });
      setLastId(externalId);
      setLog((prev) => [{ sent: text, result }, ...prev].slice(0, 12));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [channel, sender, text]);

  if (!facilityId) {
    return (
      <p className="px-4 py-6 text-[12.5px] leading-relaxed text-ink-2">
        Choose a health centre on the map to send a message as one of its
        registered handsets.
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
      <p className="text-[12.5px] leading-relaxed text-ink-2">
        Every message below runs through the same ingestion spine a real SMS
        would. Only the carrier is simulated.
      </p>

      <label className="mt-3 block text-[12px] font-medium text-ink">From</label>
      <select
        value={sender}
        onChange={(e) => setSender(e.target.value)}
        className="mt-1 h-10 w-full rounded-md border border-line bg-panel px-2 text-[12.5px] text-ink focus:border-brand focus:outline-none"
      >
        {handsets.map((h) => (
          <option key={h.number} value={h.number}>
            {h.masked} · {h.role}
          </option>
        ))}
        <option value="9000000000">9000000000 · unregistered number</option>
      </select>

      <div role="tablist" aria-label="Channel" className="mt-3 flex gap-1">
        {CHANNELS.map((ch) => (
          <button
            key={ch.id}
            role="tab"
            aria-selected={channel === ch.id}
            title={ch.hint}
            onClick={() => setChannel(ch.id)}
            className={`min-h-9 flex-1 rounded-md border px-2 text-[12px] font-medium ${
              channel === ch.id
                ? "border-brand bg-brand text-white"
                : "border-line text-ink-2"
            }`}
          >
            {ch.label}
          </button>
        ))}
      </div>
      <p className="mt-1 text-[11px] text-ink-3">
        {CHANNELS.find((ch) => ch.id === channel)?.hint}
      </p>

      <label className="mt-3 block text-[12px] font-medium text-ink">Message</label>
      <div className="mt-1 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          className="h-10 min-w-0 flex-1 rounded-md border border-line bg-panel px-2 font-mono text-[12.5px] text-ink focus:border-brand focus:outline-none"
        />
        <button
          onClick={() => send()}
          disabled={busy}
          className="h-10 shrink-0 rounded-md bg-brand px-4 text-[13px] font-medium text-white disabled:opacity-60"
        >
          {busy ? "Sending…" : "Send"}
        </button>
      </div>

      {lastId && (
        <button
          onClick={() => send(lastId)}
          disabled={busy}
          title="Sends the same message id again, as a carrier retry would"
          className="mt-2 min-h-9 w-full rounded-md border border-line px-3 text-[12px] font-medium text-ink-2 disabled:opacity-60"
        >
          Resend as a carrier retry — must not double-count
        </button>
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map((ex, i) => (
          <button
            key={`${ex.text}-${i}`}
            onClick={() => setText(ex.text)}
            title={ex.what}
            className="rounded border border-line px-2 py-1 font-mono text-[11px] text-ink-2 hover:border-brand hover:text-brand"
          >
            {ex.text}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="mt-3 text-[12.5px] text-crit">
          {error}
        </p>
      )}

      <ul className="mt-4 flex flex-col gap-2.5">
        {log.map((entry, i) => (
          <li
            key={i}
            className="rounded-lg border border-line bg-panel px-3 py-2.5"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-[12px] text-ink-2">
                → {entry.sent}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                {entry.result.duplicate && <Pill ok>duplicate</Pill>}
                <Pill ok={entry.result.accepted}>{entry.result.stage}</Pill>
              </div>
            </div>
            <p className="mt-1.5 text-[12.5px] leading-snug text-ink">
              {entry.result.reply}
            </p>
            {entry.result.readings.length > 0 && (
              <ul className="mt-1.5 flex flex-col gap-0.5">
                {entry.result.readings.map((r) => (
                  <li key={r.sku_code} className="text-[11.5px] text-ink-3">
                    <span className="font-mono text-ink-2">{r.sku_code}</span> ={" "}
                    {r.qty.toLocaleString("en-IN")}
                    {r.days_of_stock != null && (
                      <> · {r.days_of_stock.toFixed(1)} days · {r.status}</>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {entry.result.actions.length > 0 && (
              <p className="mt-1 font-mono text-[10.5px] text-ink-3">
                {entry.result.actions.join(" · ")}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

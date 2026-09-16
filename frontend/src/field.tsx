/**
 * The field client — spec items 14 and 15 (C5, C6).
 *
 * The right-hand third of the screen is the handset of someone standing in a
 * health centre. The left two thirds is what the state sees. Putting them in
 * one frame is the only way to demonstrate a two-sided system, because a judge
 * can otherwise only watch one side at a time.
 *
 * Three channels, because the people this platform is for do not all have the
 * same phone:
 *
 *   smartphone    a form, a ward photo, a geofenced check-in
 *   weak signal   the same form, queued on the device and sent when there is
 *                 signal — reports are never lost to a dead bar
 *   feature phone an SMS keypad: no app, no data, no browser
 *
 * All three end in the same database through the same pipeline. The channel
 * changes what a person can send, never what the platform does with it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  api,
  type FacilityDetail,
  type Sku,
  type User,
  can,
} from "./api";

type Channel = "smartphone" | "weak" | "feature";

const CHANNELS: { key: Channel; label: string; hint: string }[] = [
  { key: "smartphone", label: "Smartphone", hint: "App, camera and GPS on a working connection" },
  { key: "weak", label: "Weak signal", hint: "Queues on the device and sends when signal returns" },
  { key: "feature", label: "Feature phone", hint: "SMS only — no app, no data, no browser" },
];

const QUEUE_KEY = "swasthsetu.field.queue";

interface QueuedReport {
  id: string;
  facility_id: string;
  sku_code: string;
  qty: number;
  queued_at: number;
}

/** What this device can actually do right now — spec item 14.
 *
 * The browser tells us whether it is online and roughly how fast the
 * connection is. A field client that assumes a good connection is a field
 * client that loses reports, so this drives which channel is offered first.
 */
function useCapability() {
  const [online, setOnline] = useState(() => navigator.onLine);
  const [effective, setEffective] = useState<string | null>(null);

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);

    // Not in every browser, and never in Safari — treated as unknown rather
    // than assumed good.
    const conn = (navigator as { connection?: { effectiveType?: string; addEventListener?: (t: string, f: () => void) => void; removeEventListener?: (t: string, f: () => void) => void } }).connection;
    const read = () => setEffective(conn?.effectiveType ?? null);
    read();
    conn?.addEventListener?.("change", read);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
      conn?.removeEventListener?.("change", read);
    };
  }, []);

  const weak = !online || effective === "2g" || effective === "slow-2g";
  return { online, effective, weak };
}

function loadQueue(): QueuedReport[] {
  try {
    return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
  } catch {
    // A corrupt or unavailable store must not take the panel down with it.
    return [];
  }
}

function saveQueue(items: QueuedReport[]) {
  try {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(items));
  } catch {
    /* private window, or storage full: the report is still in memory */
  }
}

function Bubble({ from, children }: { from: "device" | "system"; children: React.ReactNode }) {
  const mine = from === "device";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-2.5 py-1.5 text-[12px] ${
          mine ? "bg-brand text-white" : "border border-line bg-panel text-ink"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

export function FieldPanel({
  user,
  skus,
  facility,
  demoMode,
  onSubmitted,
  onCollapse,
}: {
  user: User;
  skus: Sku[];
  /** Whose handset this is. Follows the map selection so the two sides of the
   *  screen are always talking about the same place. */
  facility: FacilityDetail | null;
  demoMode: boolean;
  onSubmitted: () => void;
  onCollapse: () => void;
}) {
  const capability = useCapability();
  const [channel, setChannel] = useState<Channel>("smartphone");
  const [sku, setSku] = useState<string>("");
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ from: "device" | "system"; text: string }[]>([]);
  const [queue, setQueue] = useState<QueuedReport[]>(loadQueue);
  const [sms, setSms] = useState("ORS 60");
  // A demo needs to show the queue without anyone unplugging the wifi. Labelled
  // as a switch, because pretending the browser went offline when it did not
  // would be the kind of small lie this project keeps refusing to tell.
  const [pretendOffline, setPretendOffline] = useState(false);
  const hasSignal = capability.online && !pretendOffline;
  const [contact, setContact] = useState<{ masked: string; demo_number: string | null } | null>(null);

  // The browser's own view of the connection picks the channel to start on.
  useEffect(() => {
    if (capability.weak) setChannel((c) => (c === "smartphone" ? "weak" : c));
  }, [capability.weak]);

  useEffect(() => {
    setLog([]);
    setContact(null);
    if (!facility) return;
    api
      .facilityContacts(facility.id)
      .then((rows) => setContact(rows.find((r) => r.role === "reporter") ?? null))
      .catch(() => setContact(null));
  }, [facility?.id]);

  useEffect(() => {
    if (!sku && skus.length) setSku(skus[0].code);
  }, [skus, sku]);

  const mayReport = useMemo(
    () =>
      facility
        ? can.report(user, {
            id: facility.id,
            state_silo: facility.state_silo,
            district: facility.district,
          })
        : false,
    [user, facility],
  );

  const say = useCallback((from: "device" | "system", text: string) => {
    setLog((l) => [...l.slice(-12), { from, text }]);
  }, []);

  /* ------------------------------------------------------- smartphone --- */

  const sendReading = async () => {
    if (!facility || !sku) return;
    const amount = Number(qty);
    if (!Number.isFinite(amount) || amount < 0) return;

    if (channel === "weak" && !hasSignal) {
      const item: QueuedReport = {
        id: `${Date.now()}`,
        facility_id: facility.id,
        sku_code: sku,
        qty: amount,
        queued_at: Date.now(),
      };
      const next = [...queue, item];
      setQueue(next);
      saveQueue(next);
      say("device", `${sku} ${amount} — held on the device`);
      say("system", "No signal. Queued; it will send itself when a bar returns.");
      setQty("");
      return;
    }

    setBusy(true);
    say("device", `${sku} ${amount}`);
    try {
      const r = await api.submitReading({
        facility_id: facility.id,
        sku_code: sku,
        qty_on_hand: amount,
        source: "form",
      });
      say(
        "system",
        `Recorded. ${r.days_of_stock != null ? `${r.days_of_stock.toFixed(0)} days of cover.` : ""}${
          r.status_changed ? ` Status is now ${r.status_after.replace("_", " ")}.` : ""
        }`,
      );
      setQty("");
      onSubmitted();
    } catch (e) {
      say("system", e instanceof ApiError ? e.message : "Could not send that report");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (hasSignal && queue.length && !busy) void flushQueue();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasSignal]);

  const flushQueue = async () => {
    if (!queue.length) return;
    setBusy(true);
    const remaining: QueuedReport[] = [];
    for (const item of queue) {
      try {
        await api.submitReading({
          facility_id: item.facility_id,
          sku_code: item.sku_code,
          qty_on_hand: item.qty,
          source: "form",
        });
        say("system", `Sent ${item.sku_code} ${item.qty} from the queue.`);
      } catch {
        remaining.push(item);
      }
    }
    setQueue(remaining);
    saveQueue(remaining);
    setBusy(false);
    onSubmitted();
  };

  /* ----------------------------------------------------- feature phone --- */

  const sendSms = async (text?: string) => {
    if (!facility || !contact?.demo_number) return;
    const body = (text ?? sms).trim();
    if (!body) return;
    setBusy(true);
    say("device", body);
    try {
      const r = await api.simulateInbound({
        channel: "sms",
        from: contact.demo_number,
        text: body,
      });
      say("system", r.reply);
      if (r.accepted) onSubmitted();
    } catch (e) {
      say("system", e instanceof ApiError ? e.message : "The message did not go through");
    } finally {
      setBusy(false);
    }
  };

  /* ----------------------------------------------------------- render --- */

  return (
    <aside className="z-[1000] flex w-[360px] shrink-0 flex-col border-l border-line bg-canvas">
      <div className="flex items-center justify-between border-b border-line bg-panel px-3 py-2">
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-ink">Field client</div>
          <div className="truncate text-[11px] text-ink-3">
            {facility ? facility.name : "Select a facility on the map"}
          </div>
        </div>
        <button
          onClick={onCollapse}
          title="Hide the field panel"
          className="shrink-0 rounded border border-line px-2 py-1 text-[11.5px] text-ink-2 hover:border-brand hover:text-ink"
        >
          Hide
        </button>
      </div>

      {/* --------------------------------------------------- the phone --- */}
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="flex min-h-0 flex-1 flex-col rounded-[20px] border-[6px] border-ink/80 bg-panel shadow-lg">
          <div className="flex items-center justify-between rounded-t-[14px] bg-ink/80 px-3 py-1 text-[10px] text-white">
            <span>
              {hasSignal ? "◼◼◼" : "◼◽◽"}{" "}
              {hasSignal ? (capability.effective ?? "wifi") : "no signal"}
            </span>
            <span>{contact?.masked ?? "unregistered"}</span>
          </div>

          <div className="flex gap-1 border-b border-line px-2 pt-2">
            {CHANNELS.map((c) => (
              <button
                key={c.key}
                title={c.hint}
                onClick={() => setChannel(c.key)}
                className={`-mb-px border-b-2 px-1.5 pb-1.5 text-[11px] font-medium ${
                  channel === c.key
                    ? "border-brand text-ink"
                    : "border-transparent text-ink-3 hover:text-ink-2"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
            {!facility ? (
              <p className="py-6 text-center text-[12px] text-ink-3">
                Click a facility on the map and this becomes that facility's phone.
              </p>
            ) : !mayReport ? (
              <p className="py-6 text-center text-[12px] text-ink-2">
                You are signed in as {user.name}, who does not report for this facility. Sign in as
                its staff to send from here.
              </p>
            ) : (
              <>
                {channel === "feature" ? (
                  <div className="space-y-1.5">
                    <p className="text-[11px] text-ink-3">
                      No app and no data — just a text. The platform parses it, matches the
                      medicine and texts back the days of cover.
                    </p>
                    {log.map((l, i) => (
                      <Bubble key={i} from={l.from}>
                        {l.text}
                      </Bubble>
                    ))}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <label className="block text-[11px] font-medium text-ink-2">
                      Medicine
                      <select
                        value={sku}
                        onChange={(e) => setSku(e.target.value)}
                        className="mt-0.5 w-full rounded border border-line bg-panel px-2 py-1.5 text-[12.5px] text-ink"
                      >
                        {skus.map((s) => (
                          <option key={s.code} value={s.code}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-[11px] font-medium text-ink-2">
                      Counted on the shelf
                      <input
                        value={qty}
                        onChange={(e) => setQty(e.target.value)}
                        inputMode="numeric"
                        placeholder="e.g. 60"
                        className="mt-0.5 w-full rounded border border-line bg-panel px-2 py-1.5 font-mono text-[12.5px] text-ink placeholder:text-ink-3"
                      />
                    </label>
                    <button
                      onClick={sendReading}
                      disabled={busy || !qty}
                      className="w-full rounded bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-50"
                    >
                      {busy
                        ? "Sending…"
                        : channel === "weak" && !hasSignal
                          ? "Hold on the device"
                          : "Send report"}
                    </button>

                    {channel === "weak" && (
                      <div className="rounded-md border border-line bg-canvas p-2">
                        <div className="flex items-center justify-between text-[11.5px]">
                          <span className="text-ink-2">
                            {queue.length
                              ? `${queue.length} report${queue.length > 1 ? "s" : ""} waiting on the device`
                              : "Nothing waiting"}
                          </span>
                          {queue.length > 0 && (
                            <button
                              onClick={flushQueue}
                              disabled={busy}
                              className="font-medium text-brand hover:underline disabled:opacity-50"
                            >
                              Send now
                            </button>
                          )}
                        </div>
                        <p className="mt-1 text-[11px] text-ink-3">
                          Reports are written to the device first, so a dead bar costs a delay and
                          never a report.
                        </p>
                        <label className="mt-1.5 flex items-center gap-1.5 text-[11px] text-ink-2">
                          <input
                            type="checkbox"
                            checked={pretendOffline}
                            onChange={(e) => setPretendOffline(e.target.checked)}
                          />
                          Simulate losing signal
                        </label>
                      </div>
                    )}

                    <div className="space-y-1.5 pt-1">
                      {log.map((l, i) => (
                        <Bubble key={i} from={l.from}>
                          {l.text}
                        </Bubble>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {channel === "feature" && facility && mayReport && (
            <div className="border-t border-line p-2">
              <div className="flex gap-1.5">
                <input
                  value={sms}
                  onChange={(e) => setSms(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !busy && void sendSms()}
                  className="min-w-0 flex-1 rounded border border-line bg-panel px-2 py-1.5 font-mono text-[12.5px] uppercase text-ink"
                />
                <button
                  onClick={() => void sendSms()}
                  disabled={busy}
                  className="shrink-0 rounded bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-50"
                >
                  Send
                </button>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {["ORS 60 ZINC 20", "ORS45", "zink 30", "HELP"].map((t) => (
                  <button
                    key={t}
                    onClick={() => {
                      setSms(t);
                      void sendSms(t);
                    }}
                    disabled={busy}
                    className="rounded border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-ink-2 hover:border-brand hover:text-ink disabled:opacity-50"
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <p className="mt-2 text-[10.5px] text-ink-3">
          {demoMode
            ? "Demo: the feature-phone channel runs the same pipeline a Twilio SMS will, with no account involved."
            : "Reports from this panel are committed like any other."}
        </p>
      </div>
    </aside>
  );
}

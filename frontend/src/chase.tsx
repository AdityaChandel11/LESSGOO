/**
 * "Chase": an officer's reminder to a centre to report what only it can.
 *
 * Fix list #74. Stock, deliveries, staff check-ins and beds are facts a centre
 * states about itself; an officer who could confirm a centre's delivery or
 * check its staff in would be both sides of a check at once. So where an
 * officer used to see those forms, they see this instead.
 *
 * The reminder is logged on the channel simulator, and the screen says so:
 * this prototype keeps only a salted hash of each handset's number, so there
 * is no number to text a real phone with.
 */

import { useState } from "react";

import { ApiError, type ChaseResult, type ChaseTopic, api } from "./api";

export function ChaseButton({
  facilityId,
  topic,
  movementId,
  label,
}: {
  facilityId: string;
  topic: ChaseTopic;
  movementId?: number;
  label: string;
}) {
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<ChaseResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const chase = async () => {
    setBusy(true);
    setError(null);
    try {
      setSent(await api.chase(facilityId, { topic, movement_id: movementId }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not send the reminder");
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <p className="mt-1.5 rounded-md border border-line bg-canvas px-2 py-1.5 text-[11.5px] leading-snug text-ink-2">
        Reminder logged for the centre's handset {sent.sent_to} on the channel simulator
        (this prototype holds no phone numbers, so no real phone is texted):{" "}
        <span className="text-ink">“{sent.body}”</span>
      </p>
    );
  }
  return (
    <div className="mt-1.5">
      <button
        onClick={() => void chase()}
        disabled={busy}
        className="rounded border border-line px-2 py-1 text-[11.5px] font-medium text-ink-2 hover:border-brand hover:text-ink disabled:opacity-50"
      >
        {busy ? "Sending…" : label}
      </button>
      {error && <p className="mt-1 text-[11.5px] text-crit">{error}</p>}
    </div>
  );
}

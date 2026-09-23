/**
 * Your own attendance, and how the system came to believe it.
 *
 * Everywhere else in this project, attendance is a count. The map shows a
 * facility's rate, the trust score reads a facility's pattern, and the event
 * that goes out when somebody checks in deliberately drops the staff
 * reference before it is published. That rule exists so that a district
 * officer cannot open a console and read a named health worker's movements,
 * and nothing on this screen weakens it: the data here comes from
 * `/me/attendance`, which takes no parameter saying whose record to return.
 * There is no version of this page that shows a colleague.
 *
 * What it is for is the other half of that bargain. A system that records
 * where you were, and then shows the record only to the people evaluating
 * you, is surveillance. The same record, shown first to the person it is
 * about, is something they can check and dispute. So this is the screen a
 * health worker opens to see what was written down about them — every shift,
 * every missed day, and every random re-verification, including the ones that
 * went unanswered.
 *
 * Three things it refuses to round up:
 *
 *   - A missed day is called a missed day and nothing more. Leave, training,
 *     a posting elsewhere and a dead handset all land in the same column, and
 *     the screen says so rather than implying absence.
 *   - A geofence that could not run is never drawn as one that passed. An IVR
 *     call carries no location; the row says "could not be placed", which is
 *     a different fact from "confirmed" and must not be shaded to look like it.
 *   - An unanswered ping is shown, not hidden. It is the reader's own record;
 *     they are the person best placed to know that the phone was in a drawer.
 */

import { useEffect, useState } from "react";

import { type SelfDay, type SelfRecord, type VerificationPing, api } from "../api";
import { both } from "./labels";

const METHOD_LABEL: Record<string, string> = {
  gps: "phone GPS",
  cell_id: "cell tower",
  simulated: "cell tower",
  none: "no location",
};

const SOURCE_LABEL: Record<string, string> = {
  form: "app",
  ussd: "USSD",
  ivr: "phone call",
  sms: "SMS",
};

/** What each outcome means, in the reader's own terms. */
const OUTCOME: Record<
  VerificationPing["outcome"],
  { label: string; tone: string; detail: string }
> = {
  confirmed: {
    label: "Confirmed at the centre",
    tone: "text-ok",
    detail: "you answered, and the location was inside the centre's boundary",
  },
  out_of_range: {
    label: "Answered from outside",
    tone: "text-risk",
    detail: "you answered, but the location was outside the centre's boundary",
  },
  unlocatable: {
    label: "Answered, location unknown",
    tone: "text-ink-2",
    detail: "a phone call carries no location, so this could not be placed",
  },
  no_reply: {
    label: "No reply",
    tone: "text-ink-2",
    detail: "nothing came back before the window closed",
  },
};

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function dayLabel(iso: string): { weekday: string; date: string; isToday: boolean } {
  const d = new Date(`${iso}T00:00:00`);
  const today = new Date().toISOString().slice(0, 10);
  return {
    weekday: d.toLocaleDateString("en-IN", { weekday: "short" }),
    date: d.toLocaleDateString("en-IN", { day: "numeric", month: "short" }),
    isToday: iso === today,
  };
}

/* ------------------------------------------------------------ the header --- */

function Figure({
  value,
  label,
  tone = "text-ink",
}: {
  value: number | string;
  label: string;
  tone?: string;
}) {
  return (
    <div>
      <p className={`font-mono text-[19px] leading-none font-semibold tabular-nums ${tone}`}>
        {value}
      </p>
      <p className="mt-1 text-[11px] leading-snug text-ink-3">{label}</p>
    </div>
  );
}

/* --------------------------------------------------------------- one day --- */

function Ping({ ping }: { ping: VerificationPing }) {
  const meta = OUTCOME[ping.outcome];
  const channel = ping.channel === "sms" ? "SMS" : "Automated call";
  return (
    <li className="flex gap-2 border-t border-line/70 pt-1.5">
      <span className="mt-[3px] shrink-0 font-mono text-[10.5px] tabular-nums text-ink-3">
        {time(ping.sent_at)}
      </span>
      <span className="min-w-0">
        <span className="text-[11.5px] text-ink-2">
          {channel} check{" "}
          <span className={`font-medium ${meta.tone}`}>· {meta.label}</span>
        </span>
        <span className="mt-0.5 block text-[11px] leading-snug text-ink-3">
          {meta.detail}
          {ping.responded_at && ` · replied ${time(ping.responded_at)}`}
          {/* The measured distance, only when one was actually measured. */}
          {ping.geofence_km !== null && ` · ${ping.geofence_km.toFixed(2)} km away`}
          {ping.cell_id && ` · tower ${ping.cell_id}`}
        </span>
      </span>
    </li>
  );
}

function Day({ day }: { day: SelfDay }) {
  const { weekday, date, isToday } = dayLabel(day.day);

  return (
    <li className="flex gap-3 border-b border-line py-2.5 last:border-b-0">
      <div className="w-14 shrink-0 pt-0.5">
        <p className="text-[11px] font-medium text-ink-2">
          {weekday}
          {isToday && <span className="ml-1 text-brand">today</span>}
        </p>
        <p className="font-mono text-[11px] tabular-nums text-ink-3">{date}</p>
      </div>

      <div className="min-w-0 flex-1">
        {!day.present ? (
          <>
            <p className="text-[12.5px] font-medium text-ink-2">
              {both("noCheckIn")}
            </p>
            {/* Deliberately not "absent". The record knows that nothing was
                logged; it does not know why, and saying so is the difference
                between a record and an accusation. */}
            <p className="mt-0.5 text-[11px] leading-snug text-ink-3">
              Nothing was logged for this day. Leave, training, a posting elsewhere
              and a phone that could not connect all look the same here.
            </p>
          </>
        ) : (
          <>
            <p className="text-[12.5px] text-ink">
              <span className="font-medium capitalize">{day.shift ?? "Shift"}</span>
              {day.checked_in_at && (
                <span className="font-mono tabular-nums text-ink-2">
                  {" "}
                  {time(day.checked_in_at)}
                  {day.checked_out_at ? ` – ${time(day.checked_out_at)}` : " – still open"}
                </span>
              )}
            </p>

            <p className="mt-0.5 text-[11px] leading-snug text-ink-3">
              Checked in by {SOURCE_LABEL[day.source ?? ""] ?? day.source ?? "unknown"}
              {day.loc_method && ` · located by ${METHOD_LABEL[day.loc_method] ?? day.loc_method}`}
              {day.geofence_ok === true && (
                <span className="text-ok"> · inside the centre</span>
              )}
              {day.geofence_ok === false && (
                <span className="text-risk"> · outside the centre</span>
              )}
              {/* Never a tick, never a cross: the check did not run. */}
              {day.geofence_ok === null && " · location could not be checked"}
              {day.geofence_km !== null && ` (${day.geofence_km.toFixed(2)} km)`}
            </p>

            {day.pings.length > 0 && (
              <ul className="mt-1.5 space-y-1.5">
                {day.pings.map((p) => (
                  <Ping key={p.sent_at} ping={p} />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </li>
  );
}

/* ------------------------------------------------------------- the screen --- */

export default function MyAttendance({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<SelfRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .myAttendance()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  if (error) {
    return (
      <p role="alert" className="text-[12.5px] leading-relaxed text-ink-2">
        Your attendance record could not be loaded. {error}
      </p>
    );
  }
  if (!data) {
    return (
      <p className="text-[12.5px] text-ink-3" aria-live="polite">
        Loading your record…
      </p>
    );
  }

  const answered = data.pings_sent - data.pings_unanswered;

  return (
    <div>
      <section
        aria-label="Your attendance, last 30 days"
        className="rounded-lg border border-line bg-panel px-3.5 py-3"
      >
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
          {both("yourAttendance")}
        </h2>
        <p className="mt-0.5 text-[11.5px] text-ink-3">
          Last {data.window_days} days at {data.facility_name}
        </p>

        <div className="mt-3 grid grid-cols-3 gap-3">
          <Figure value={data.days_present} label="days you checked in" />
          <Figure
            value={data.days_absent}
            label="days with no check-in"
            tone={data.days_absent > 0 ? "text-ink-2" : "text-ink"}
          />
          <Figure
            value={`${answered}/${data.pings_sent}`}
            label="random checks answered"
          />
        </div>

        {data.pings_sent > 0 && (
          <p className="mt-3 border-t border-line pt-2 text-[11.5px] leading-snug text-ink-2">
            {both("randomChecks")}: an SMS or an automated call arrives at an
            unannounced moment during a shift. Answering it from the centre is what
            turns a check-in into a confirmed one.{" "}
            <span className="text-ink-3">
              {data.pings_confirmed} of {data.pings_sent} were confirmed at the centre
              {data.pings_unanswered > 0 && `, ${data.pings_unanswered} went unanswered`}
              .
            </span>
          </p>
        )}
      </section>

      {/* The standing guarantee, said where it applies rather than in a policy
          document nobody opens. */}
      <p className="mt-2.5 rounded-md border border-line bg-panel px-3 py-2 text-[11px] leading-snug text-ink-3">
        {both("onlyYou")} Your district officer sees how many people were present at
        this centre and how their attendance was verified — never a named person's
        record, and never this page.
      </p>

      <h3 className="mt-4 mb-1 text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
        {both("dayByDay")}
      </h3>
      <ul className="rounded-lg border border-line bg-panel px-3.5">
        {data.days.map((d) => (
          <Day key={d.day} day={d} />
        ))}
      </ul>

      <p className="mt-3 text-[11px] leading-snug text-ink-3">
        If a day here is wrong, tell your district officer — the record is corrected
        at the centre, not overwritten from a console.
      </p>
    </div>
  );
}

/**
 * Who is here today, and how the system knows.
 *
 * Read-only, and deliberately so. Attendance is already captured elsewhere —
 * by the check-in form, by an SMS reading `IN`, by a USSD gateway that carries
 * a cell tower — and this card adds no way to record it. It exists because the
 * pharmacist's screen is the one somebody actually looks at, and attendance
 * that only appears on a district officer's console is attendance the centre
 * itself never sees.
 *
 * It says counts and never a name. `staff_ref` is pseudonymous in the database
 * and is not on this wire at all: the endpoint returns roster size, how many
 * checked in, and how they were located. That is the standing rule for this
 * whole subsystem (spec rule 8 — flags attach to a facility and a pattern,
 * never to a person), and the closing line says so on screen, because a
 * guarantee nobody can read is a guarantee nobody believes.
 *
 * Two things it refuses to round up. A geofence that could not run is never
 * displayed as one that passed — a phone call carries no location, so a centre
 * reporting by IVR gets told plainly that its attendance cannot be placed here.
 * And the footfall contradiction is phrased as a question, not a finding: staff
 * present with no patients logged is far more often an unfilled register than
 * an absent team.
 */

import { useEffect, useState } from "react";

import { type Attendance, api } from "../api";
import { both } from "./labels";

const METHOD_LABEL: Record<string, string> = {
  gps: "phone GPS",
  cell_id: "cell tower",
  simulated: "cell tower (simulated)",
  none: "no location",
};

export default function Team({
  facilityId,
  refreshKey,
}: {
  facilityId: string;
  refreshKey: number;
}) {
  const [data, setData] = useState<Attendance | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    api
      .attendance(facilityId)
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [facilityId, refreshKey]);

  // This card is a supporting panel on a screen about medicines. If attendance
  // cannot be read, the shelf is still the point — so it withdraws quietly
  // rather than pushing an error above the stock cards.
  if (failed || !data) return null;

  const methods = Object.entries(data.by_method).filter(([, n]) => n > 0);
  const unverifiable = (data.by_method.none ?? 0) > 0 && data.geofence_checked === 0;

  return (
    <section
      aria-label="Today's team"
      className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
    >
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
        {both("todaysTeam")}
      </h2>

      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="font-mono text-[19px] leading-none font-semibold tabular-nums text-ink">
          {data.present}
        </span>
        <span className="text-[12.5px] text-ink-2">
          of {data.roster} on the roster checked in today
        </span>
      </div>

      {data.footfall_today !== null && (
        <p className="mt-1 text-[11.5px] text-ink-3">
          {both("patientsLogged")}: {data.footfall_today}
        </p>
      )}

      {methods.length > 0 && (
        <p className="mt-1 text-[11.5px] leading-snug text-ink-3">
          Located by{" "}
          {methods.map(([m, n]) => `${METHOD_LABEL[m] ?? m} (${n})`).join(", ")}
          {/* Only stated when a geofence actually ran. A centre with nothing to
              check says nothing here, rather than showing "0 of 0 confirmed"
              and inviting it to be read as a failure. */}
          {data.geofence_checked > 0 && (
            <>
              {" · "}
              <span className="text-ink-2">
                {data.geofence_pass} of {data.geofence_checked} confirmed inside this
                centre's geofence
              </span>
            </>
          )}
        </p>
      )}

      {unverifiable && (
        <p className="mt-1.5 text-[11.5px] leading-snug text-ink-2">
          This centre reports by phone call, which carries no location. Attendance is
          recorded, but nothing here can place it at the centre.
        </p>
      )}

      {data.contradiction && (
        <p className="mt-2 rounded-md border border-risk/30 bg-risk/10 px-2 py-1.5 text-[11.5px] leading-snug text-ink-2">
          {data.contradiction}
        </p>
      )}

      <p className="mt-2 border-t border-line pt-2 text-[11px] leading-snug text-ink-3">
        Counts only. Attendance is never shown, scored or exported per person.
      </p>
    </section>
  );
}

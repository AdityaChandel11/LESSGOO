/**
 * The ward, from the centre's own side.
 *
 * Read-only, and the capture it describes happens elsewhere. A ward photo is
 * taken on the officer console's bed panel, where the Gemini pipeline reads
 * the bed count and the rotating code out of the image in one pass. This
 * screen is what the people in the building see afterwards: what was reported,
 * what the model made of it, and — the part that matters most here — today's
 * verification code, which has to be visible in the photograph for the report
 * to count as verified at all.
 *
 * The code is the reason this tab exists on the phone rather than only on the
 * console. It is generated server-side each morning and delivered by SMS, and
 * the person holding the camera is the person who needs to read it. A code
 * that only ever appears on a district officer's screen is a code nobody can
 * write on the whiteboard.
 *
 * What it will not do is present an unverified report as a verified one. A
 * photograph whose code could not be read, or was yesterday's, is shown with
 * that stated plainly — the whole point of the rotating code is that it
 * catches a reused photo, and a UI that softens the result throws that away.
 */

import { useEffect, useState } from "react";

import { type BedCode, type BedReport, type FacilityDetail, api } from "../api";
import { both } from "./labels";

const VERIFICATION: Record<
  BedReport["verification"],
  { label: string; tone: string }
> = {
  verified: { label: "Verified", tone: "text-ok" },
  unverified: { label: "Not verified", tone: "text-risk" },
  rejected: { label: "Rejected", tone: "text-crit" },
};

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function Beds({
  facilityId,
  facility,
  refreshKey,
}: {
  facilityId: string;
  facility: FacilityDetail | null;
  refreshKey: number;
}) {
  const [code, setCode] = useState<BedCode | null>(null);
  const [reports, setReports] = useState<BedReport[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    Promise.all([api.bedCode(facilityId), api.bedReports(facilityId, 8)])
      .then(([c, r]) => alive && (setCode(c), setReports(r)))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [facilityId, refreshKey]);

  const total = facility?.beds_total ?? null;
  const occupied = facility?.beds_occupied ?? null;
  const free = total !== null && occupied !== null ? total - occupied : null;

  return (
    <div>
      <section
        aria-label="Bed occupancy"
        className="rounded-lg border border-line bg-panel px-3.5 py-3"
      >
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
          {both("bedsOccupied")}
        </h2>
        {occupied === null || total === null ? (
          <p className="mt-1.5 text-[12.5px] text-ink-2">
            No ward report yet today. The count below is whatever was last confirmed.
          </p>
        ) : (
          <>
            <div className="mt-1.5 flex items-baseline gap-2">
              <span className="font-mono text-[22px] leading-none font-semibold tabular-nums text-ink">
                {occupied}
              </span>
              <span className="text-[12.5px] text-ink-2">of {total} beds</span>
            </div>
            <p className="mt-1 text-[11.5px] text-ink-3">
              {both("bedsFree")}: {free}
            </p>
          </>
        )}
      </section>

      {code && (
        <section
          aria-label="Today's verification code"
          className="mt-2.5 rounded-lg border border-line bg-panel px-3.5 py-3"
        >
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
            {both("todaysCode")}
          </h2>
          <p className="mt-1.5 font-mono text-[24px] leading-none font-semibold tracking-[0.18em] tabular-nums text-brand">
            {code.code}
          </p>
          <p className="mt-2 text-[11.5px] leading-snug text-ink-2">
            Write this on the ward whiteboard before photographing the beds. It changes
            every morning, so a photograph taken yesterday cannot be sent today — that
            is the only thing standing between a ward report and a reused picture.
          </p>
          <p className="mt-1 text-[11px] text-ink-3">
            {/* Says how it reached the centre, or admits it has not. The
                delivery string is a whole sentence from the server, so it
                carries its own verb and takes none from here. */}
            {code.delivered_at
              ? `${code.delivery} · ${when(code.delivered_at)}`
              : `${code.delivery} — not delivered yet today, but it is on this screen regardless`}
          </p>
        </section>
      )}

      <h3 className="mt-4 mb-1 text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
        {both("lastWardReport")}
      </h3>

      {error && (
        <p role="alert" className="text-[12.5px] text-ink-2">
          Ward reports could not be loaded. {error}
        </p>
      )}
      {!error && reports?.length === 0 && (
        <p className="text-[12.5px] text-ink-3">
          No ward photographs have been submitted for this centre yet.
        </p>
      )}

      {reports && reports.length > 0 && (
        <ul className="rounded-lg border border-line bg-panel px-3.5">
          {reports.map((r) => {
            const v = VERIFICATION[r.verification];
            return (
              <li key={r.id} className="border-b border-line py-2.5 last:border-b-0">
                <p className="text-[12.5px] text-ink">
                  <span className="font-medium capitalize">{r.ward}</span>
                  {r.beds_occupied !== null && r.beds_total !== null && (
                    <span className="font-mono tabular-nums text-ink-2">
                      {" "}
                      {r.beds_occupied}/{r.beds_total} occupied
                    </span>
                  )}
                  <span className={`ml-1.5 text-[11.5px] font-medium ${v.tone}`}>
                    · {v.label}
                  </span>
                </p>
                <p className="mt-0.5 text-[11px] leading-snug text-ink-3">
                  {when(r.reported_at)} · by {r.source}
                  {/* Each check reported separately, because they fail
                      separately and for different reasons. */}
                  {r.code_ok === true && " · code matched"}
                  {r.code_ok === false && " · code did not match"}
                  {r.code_ok === null && " · no code read"}
                  {r.geofence_ok === true && " · at the centre"}
                  {r.geofence_ok === false && " · away from the centre"}
                  {r.register_admissions !== null &&
                    ` · register says ${r.register_admissions}`}
                </p>
                {/* Never hidden: a count the model did not produce must not be
                    allowed to look like one it did. */}
                {r.model && (
                  <p className="mt-0.5 text-[11px] text-ink-3">
                    Read by {r.model}
                    {r.model_confidence !== null &&
                      ` · confidence ${(r.model_confidence * 100).toFixed(0)}%`}
                  </p>
                )}
                {r.reasons.length > 0 && (
                  <p className="mt-0.5 text-[11px] leading-snug text-ink-2">
                    {r.reasons.join(" · ")}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Staff attendance in the facility drawer — spec 26.1.
 *
 * The number of people present is the easy half. The half that matters is how
 * each of them was located, because that is what separates a verified
 * attendance record from a claim: GPS puts someone in the compound, a cell
 * tower puts them in the district, and an IVR call puts them nowhere at all.
 * The panel says which, plainly, and never names an individual.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type Attendance, type User, can } from "./api";

const METHOD_LABEL: Record<string, string> = {
  gps: "phone GPS",
  cell_id: "cell tower",
  simulated: "cell tower (simulated)",
  none: "no location",
};

export function AttendancePanel({
  facilityId,
  facility,
  user,
  refreshKey,
  demoMode,
}: {
  facilityId: string;
  facility: { lat: number; lng: number; state_silo: string; district: string };
  user: User;
  refreshKey: number;
  demoMode: boolean;
}) {
  const [data, setData] = useState<Attendance | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mayReport = can.report(user, {
    id: facilityId,
    state_silo: facility.state_silo,
    district: facility.district,
  });

  const load = useCallback(() => {
    api
      .attendance(facilityId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load attendance"));
  }, [facilityId]);

  useEffect(load, [load, refreshKey]);

  const checkIn = async (kind: "gps" | "ivr") => {
    setBusy(kind);
    setError(null);
    try {
      await api.checkin(facilityId, {
        // A pseudonymous reference. Attendance is reported as a facility
        // pattern; no screen in this system names a person.
        staff_ref: `${facilityId}-S${Math.floor(Math.random() * 6).toString().padStart(2, "0")}`,
        action: "in",
        shift: "morning",
        source: kind === "gps" ? "form" : "ivr",
        location:
          kind === "gps"
            ? { lat: facility.lat + 0.0004, lng: facility.lng - 0.0003, accuracy_m: 9, method: "gps" }
            : { method: "none" },
      });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not record the check-in");
    } finally {
      setBusy(null);
    }
  };

  if (!data) return null;

  const methods = Object.entries(data.by_method).filter(([, n]) => n > 0);
  const unverifiable = (data.by_method.none ?? 0) > 0 && data.geofence_checked === 0;

  return (
    <div className="border-t border-line px-4 py-3">
      <span className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
        Staff on duty
      </span>

      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="font-mono text-[19px] font-semibold tabular-nums text-ink">
          {data.present}
        </span>
        <span className="text-[12px] text-ink-2">
          of {data.roster} on the roster checked in today
        </span>
        {data.footfall_today != null && (
          <span className="ml-auto shrink-0 text-[11.5px] text-ink-3">
            {data.footfall_today} patients logged
          </span>
        )}
      </div>

      {methods.length > 0 && (
        <p className="mt-1 text-[11.5px] text-ink-3">
          Located by {methods.map(([m, n]) => `${METHOD_LABEL[m] ?? m} (${n})`).join(", ")}
          {data.geofence_checked > 0 &&
            ` · ${data.geofence_pass} of ${data.geofence_checked} inside the facility geofence`}
        </p>
      )}

      {unverifiable && (
        <p className="mt-1 text-[11.5px] text-ink-2">
          This facility reports by phone call, which carries no location — attendance here is
          recorded but cannot be placed at the facility.
        </p>
      )}

      {data.contradiction && (
        <p className="mt-1.5 rounded-md border border-risk/30 bg-risk/10 px-2 py-1.5 text-[11.5px] text-ink-2">
          {data.contradiction}
        </p>
      )}

      {mayReport && demoMode && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button
            disabled={busy !== null}
            onClick={() => void checkIn("gps")}
            title="A smartphone check-in, geofenced against the facility"
            className="rounded border border-line px-2 py-1 text-[11.5px] text-ink-2 hover:border-brand hover:text-ink disabled:opacity-50"
          >
            {busy === "gps" ? "Sending…" : "Check in from the facility"}
          </button>
          <button
            disabled={busy !== null}
            onClick={() => void checkIn("ivr")}
            title="A feature-phone call: no tower id arrives with an inbound call"
            className="rounded border border-line px-2 py-1 text-[11.5px] text-ink-2 hover:border-brand hover:text-ink disabled:opacity-50"
          >
            {busy === "ivr" ? "Sending…" : "Check in by phone call"}
          </button>
        </div>
      )}
      {error && <p className="mt-1.5 text-[11.5px] text-crit">{error}</p>}
    </div>
  );
}

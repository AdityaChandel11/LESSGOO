/**
 * Ward bed occupancy, with the evidence behind it — spec 26.2.
 *
 * A bed count on its own is a number someone typed. What makes it worth acting
 * on is the three checks beside it: today's rotating code read back out of the
 * photo, the location it was taken from, and the admission register it should
 * agree with. Each check shows what it actually concluded, including "could not
 * run", because a check that did not happen must never look like one that passed.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type BedCode, type BedReport, type User, can } from "./api";
import { drawWardBoard, plausibleOccupancy } from "./wardboard";

const VERIFICATION_TONE: Record<string, { label: string; className: string }> = {
  verified: { label: "Verified", className: "text-ok bg-ok/10 border-ok/30" },
  unverified: { label: "Unverified", className: "text-risk bg-risk/10 border-risk/30" },
  rejected: { label: "Rejected", className: "text-crit bg-crit/10 border-crit/30" },
};

/** tri-state: true passed, false failed, null could not run */
function Check({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) {
  const mark = ok === true ? "✓" : ok === false ? "✕" : "–";
  const color = ok === true ? "text-ok" : ok === false ? "text-crit" : "text-ink-3";
  return (
    <div className="flex items-baseline gap-1.5 text-[11.5px]" title={detail}>
      <span className={`font-mono font-semibold ${color}`}>{mark}</span>
      <span className={ok === null ? "text-ink-3" : "text-ink-2"}>{label}</span>
    </div>
  );
}

function when(iso: string): string {
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/**
 * Demo submissions, one per path the verification logic can take.
 *
 * Both columns exercise the same three checks; what differs is what reaches
 * the model. Under the mock extractor nothing is analysed and `simulate`
 * carries what a camera would have seen. Under a live model the browser draws
 * the ward board (see wardboard.ts) and sends it as a real image, so the
 * counts and the code come back from Gemini rather than from us — which is
 * also the only path the server will accept while a model is configured.
 */
const SIMULATIONS: { key: string; label: string; hint: string; needsPhoto: boolean }[] = [
  { key: "good", label: "Today's photo", hint: "Correct code, taken at the facility", needsPhoto: true },
  { key: "stale", label: "Yesterday's photo", hint: "An old code — should be rejected", needsPhoto: true },
  { key: "elsewhere", label: "Photo from elsewhere", hint: "Right code, wrong place", needsPhoto: true },
  // A voice call carries no image, so there is nothing for a live model to
  // read. Offered only against the mock extractor, where the whole submission
  // is simulated anyway — sending a drawn board under a "phone call" label
  // would be claiming a channel did something it cannot do.
  { key: "ivr", label: "Reported by phone call", hint: "No location available on this channel", needsPhoto: false },
];

export function BedPanel({
  facilityId,
  facility,
  user,
  refreshKey,
  demoMode,
  llmMode,
}: {
  facilityId: string;
  facility: {
    name: string;
    lat: number;
    lng: number;
    state_silo: string;
    district: string;
    /** Capacity on the facility register — what the photo is counted against. */
    beds_total: number;
  };
  user: User;
  refreshKey: number;
  demoMode: boolean;
  llmMode: "live" | "mock";
}) {
  const [reports, setReports] = useState<BedReport[]>([]);
  const [code, setCode] = useState<BedCode | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const mayReport = can.report(user, {
    id: facilityId,
    state_silo: facility.state_silo,
    district: facility.district,
  });

  const load = useCallback(() => {
    api
      .bedReports(facilityId)
      .then(setReports)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load bed reports"));
  }, [facilityId]);

  useEffect(load, [load, refreshKey]);

  // The day's code is what the demo submissions are checked against, so it has
  // to be in hand before those buttons can do anything.
  useEffect(() => {
    if (!mayReport || !demoMode || code) return;
    api
      .bedCode(facilityId)
      .then(setCode)
      .catch(() => setCode(null));
  }, [mayReport, demoMode, code, facilityId]);

  const submit = async (kind: string) => {
    if (!code) return;
    setBusy(kind);
    setError(null);
    // A code from an earlier day. Reversing today's gives a well-formed code
    // that is not today's, which is exactly the condition the check tests.
    const yesterday = code.code.split("").reverse().join("");
    // Count against the beds this facility actually has on the register, so a
    // demo never manufactures a capacity mismatch that is not the point.
    const beds_total = facility.beds_total;
    const beds_occupied = plausibleOccupancy(beds_total);

    // Where the report claims to have been taken from. Identical either way:
    // the geofence is checked server-side against the facility's registered
    // coordinates and has nothing to do with which model read the image.
    const location =
      kind === "elsewhere"
        ? { lat: facility.lat + 0.12, lng: facility.lng + 0.09, accuracy_m: 14, method: "gps" }
        : kind === "ivr"
          ? { method: "none" }
          : { lat: facility.lat, lng: facility.lng, accuracy_m: 12, method: "gps" };

    let body: Record<string, unknown>;
    if (llmMode === "live") {
      // A real image, drawn here and read by the model. Nothing tells Gemini
      // what is written on the board; the counts and the code in the response
      // are what it saw.
      const image_base64 = drawWardBoard({
        beds_total,
        beds_occupied,
        code: kind === "stale" ? yesterday : code.code,
        ward: "general",
      });
      body = { image_base64, image_mime: "image/jpeg", location };
      if (kind === "ivr") body.source = "ivr";
    } else {
      // No photograph is analysed; the stored row records model="mock".
      body = { simulate: { code_read: kind === "stale" ? yesterday : code.code, beds_occupied, beds_total }, location };
      if (kind === "ivr") body.source = "ivr";
    }
    try {
      await api.submitBedReport(facilityId, body);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not submit the report");
    } finally {
      setBusy(null);
    }
  };

  const latest = reports[0];
  const lastVerified = reports.find((r) => r.verification === "verified");

  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
          Ward beds
        </span>
        {reports.length > 0 && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="text-[11.5px] font-medium text-brand hover:underline"
          >
            {open ? "Hide detail" : "How this is verified"}
          </button>
        )}
      </div>

      {!latest ? (
        <p className="mt-1.5 text-[12px] text-ink-3">No ward photo has been submitted yet.</p>
      ) : (
        <>
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-[19px] font-semibold tabular-nums text-ink">
              {lastVerified?.beds_occupied ?? "—"}
            </span>
            <span className="text-[12px] text-ink-2">
              of {lastVerified?.beds_total ?? latest.beds_total ?? "—"} beds counted in the photo
              {facility.beds_total > 0 && (
                <span className="text-ink-3"> · {facility.beds_total} registered</span>
              )}
            </span>
            <span
              className={`ml-auto shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                VERIFICATION_TONE[latest.verification]?.className ?? ""
              }`}
            >
              {VERIFICATION_TONE[latest.verification]?.label ?? latest.verification}
            </span>
          </div>
          <p className="mt-0.5 text-[11.5px] text-ink-3">
            {lastVerified
              ? `Last verified ${when(lastVerified.reported_at)}`
              : "No verified count in the recent history"}
            {latest.verification !== "verified" && ` · latest attempt ${when(latest.reported_at)}`}
          </p>

          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            <Check
              ok={latest.code_ok}
              label="Today's code"
              detail={
                latest.code_ok === null
                  ? "The code was not legible in the photo"
                  : `Read “${latest.code_read ?? "—"}”`
              }
            />
            <Check
              ok={latest.geofence_ok}
              label={
                latest.geofence_ok === null
                  ? "Location unavailable"
                  : `At the facility${latest.geofence_km != null ? ` (${latest.geofence_km.toFixed(2)} km)` : ""}`
              }
              detail={
                latest.loc_method === "none"
                  ? "This channel carries no location"
                  : `Location from ${latest.loc_method}`
              }
            />
            <Check
              ok={latest.register_admissions == null ? null : latest.reasons.some((r) => r.includes("register")) ? false : true}
              label="Matches register"
              detail={
                latest.register_admissions == null
                  ? "No register entry for this period"
                  : `${latest.register_admissions} admissions logged`
              }
            />
          </div>

          {latest.reasons.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {latest.reasons.map((r) => (
                <li key={r} className="text-[11.5px] text-ink-2">
                  {r}
                </li>
              ))}
            </ul>
          )}

          {open && (
            <div className="mt-2.5 rounded-md border border-line bg-canvas p-2.5">
              <p className="text-[11.5px] text-ink-2">
                Each morning the server issues this facility a four-character code and sends it by
                SMS. Staff write it on the ward whiteboard and photograph the ward. One Gemini pass
                counts the beds and reads the code back out of the same image — so an old photo
                fails, because yesterday's code cannot appear in it.
              </p>
              {latest.model === "mock" ? (
                <p className="mt-1.5 text-[11px] text-ink-3">
                  This deployment is running the mock extractor: no photograph was analysed, and
                  these counts are simulated.
                </p>
              ) : (
                <p className="mt-1.5 text-[11px] text-ink-3">
                  Read by <span className="font-mono text-ink-2">{latest.model}</span>
                  {latest.model_confidence != null &&
                    ` · the model put its own confidence in the counts at ${Math.round(latest.model_confidence * 100)}%`}
                  . It returned {latest.beds_occupied ?? "—"} of {latest.beds_total ?? "—"} beds and
                  read the code as “{latest.code_read ?? "—"}”.
                </p>
              )}
              <ul className="mt-1.5 space-y-0.5">
                {reports.slice(0, 6).map((r) => (
                  <li key={r.id} className="flex items-baseline gap-2 text-[11px]">
                    <span className="w-20 shrink-0 text-ink-3">{when(r.reported_at)}</span>
                    <span
                      className={
                        r.verification === "verified"
                          ? "text-ok"
                          : r.verification === "rejected"
                            ? "text-crit"
                            : "text-risk"
                      }
                    >
                      {VERIFICATION_TONE[r.verification]?.label}
                    </span>
                    <span className="text-ink-2">
                      {r.beds_occupied ?? "—"}/{r.beds_total ?? "—"} · {r.source}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {mayReport && demoMode && (
        <div className="mt-2.5">
          {code && (
            <p className="text-[11px] text-ink-3">
              Today's code for this facility:{" "}
              <span className="font-mono font-semibold text-ink-2">{code.code}</span> · {code.delivery}
            </p>
          )}
          <p className="mt-1 text-[11px] leading-snug text-ink-3">
            {llmMode === "live"
              ? "The board below is drawn in this browser with that code and sent to Gemini as an image. The counts and the code in the result are what the model read, not what was drawn."
              : "This deployment runs the mock extractor: no image is produced and no model is called."}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {SIMULATIONS.filter((s) => llmMode === "mock" || s.needsPhoto).map((s) => (
              <button
                key={s.key}
                title={s.hint}
                disabled={busy !== null || !code}
                onClick={() => {
                  setOpen(true);
                  void submit(s.key);
                }}
                className="rounded border border-line px-2 py-1 text-[11.5px] text-ink-2 hover:border-brand hover:text-ink disabled:opacity-50"
              >
                {busy === s.key ? "Sending…" : s.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {error && <p className="mt-1.5 text-[11.5px] text-crit">{error}</p>}
    </div>
  );
}

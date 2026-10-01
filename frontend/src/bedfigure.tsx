/**
 * A centre's one bed figure (fix #68) — the same on the pharmacist's Beds tab
 * and in the officer's drawer, from the same server rule: the latest
 * *verified* ward count, with the time it was verified. Older than a day it is
 * stale and not counted as available. The latest report that did not verify is
 * shown separately beneath it, and is never counted.
 */

import { type BedReport } from "./api";

const NOT_COUNTED: Record<BedReport["verification"], string> = {
  verified: "Verified",
  unverified: "Not verified",
  rejected: "Rejected",
};

function stamp(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** The newest report, when it did not verify and is newer than the count. */
export function pendingReport(
  reports: BedReport[] | null,
  verifiedAt: string | null,
): BedReport | null {
  const latest = reports?.[0];
  if (!latest || latest.verification === "verified") return null;
  if (verifiedAt && new Date(latest.reported_at) <= new Date(verifiedAt)) return null;
  return latest;
}

export default function BedFigure({
  total,
  occupied,
  verifiedAt,
  stale,
  reports,
  large = false,
}: {
  total: number;
  occupied: number | null;
  verifiedAt: string | null;
  stale: boolean;
  /** Newest first, as the bed-reports endpoint returns them. */
  reports: BedReport[] | null;
  large?: boolean;
}) {
  const pending = pendingReport(reports, verifiedAt);

  return (
    <div>
      {occupied === null || verifiedAt === null ? (
        <p className="mt-1.5 text-[12.5px] text-ink-2">
          No verified ward count in the last 30 days · {total} beds registered
        </p>
      ) : (
        <>
          <div className="mt-1.5 flex items-baseline gap-2">
            <span
              className={`font-mono leading-none font-semibold tabular-nums text-ink ${
                large ? "text-[22px]" : "text-[19px]"
              }`}
            >
              {occupied}
            </span>
            <span className="text-[12.5px] text-ink-2">of {total} beds occupied</span>
          </div>
          <p className="mt-1 text-[11.5px] text-ink-3">
            Verified by photo · as of {stamp(verifiedAt)}
          </p>
          {stale ? (
            <p className="mt-0.5 text-[11.5px] font-medium text-risk">
              Stale — older than a day, not counted as available
            </p>
          ) : (
            <p className="mt-0.5 text-[11.5px] text-ink-2">
              Free now: {Math.max(0, total - occupied)}
            </p>
          )}
        </>
      )}
      {pending && (
        <p className="mt-1.5 border-t border-line pt-1.5 text-[11.5px] text-ink-2">
          Latest report, not counted:{" "}
          <span className="font-mono tabular-nums">
            {pending.beds_occupied ?? "—"}/{pending.beds_total ?? "—"}
          </span>{" "}
          · {NOT_COUNTED[pending.verification]} · {stamp(pending.reported_at)}
        </p>
      )}
    </div>
  );
}

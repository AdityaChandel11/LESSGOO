import type { Verification } from "./api";

/**
 * Whether a second record agrees with this row, and why. The server builds
 * the sentence from the row's own fields (verification.py); nothing is shown
 * for a row it sent no verdict for. Said in words, so it does not rest on
 * colour.
 */
export function VerifiedLine({ v }: { v?: Verification | null }) {
  if (!v) return null;
  return (
    <p className="mt-1 text-[11px] leading-snug text-ink-2">
      <span
        className={`mr-1.5 inline-block rounded-full border px-1.5 py-px text-[10.5px] font-medium ${
          v.verified ? "border-ok/30 bg-ok/10 text-ok" : "border-risk/30 bg-risk/10 text-risk"
        }`}
      >
        {v.verified ? "✓ Verified" : "Unverified"}
      </span>
      {v.reason}
    </p>
  );
}

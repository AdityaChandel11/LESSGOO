/**
 * Photograph a bill, and the shelf updates.
 *
 * The consumption half of the two-sided ledger. A delivery slip or a bill is
 * the thing a pharmacist already has in their hand; typing it into a form is
 * the step that gets skipped at the end of a long shift. So the camera does
 * it, and the same Gemini pipeline that reads a ward photo reads this one.
 *
 * What this screen is careful about is the difference between *read* and
 * *accepted*. Every line the model found is shown, including the ones that
 * were refused, with the reason. A medicine that did not match this centre's
 * list is never quietly dropped and never guessed at — an invented quantity on
 * a stock ledger is worse than no reading, because the reorder threshold, the
 * forecast and the redistribution solver all read it next and none of them can
 * tell an invented row from a real one.
 *
 * The photograph itself is never uploaded anywhere but here, and never stored.
 * Only what was read from it is.
 */

import { useRef, useState } from "react";

import { ApiError, type StockPhotoResult, api } from "../api";

export default function StockPhoto({
  facilityId,
  onCommitted,
}: {
  facilityId: string;
  onCommitted: () => void;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<StockPhotoResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = async (file: File) => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const base64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("Could not read that file"));
        // readAsDataURL gives "data:image/jpeg;base64,XXXX"; the server wants
        // only the payload.
        reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
        reader.readAsDataURL(file);
      });
      const r = await api.stockPhoto(facilityId, {
        image_base64: base64,
        image_mime: file.type || "image/jpeg",
      });
      setResult(r);
      if (r.committed > 0) onCommitted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <section
      aria-label="Update stock from a bill"
      className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
    >
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
        From a bill or delivery slip
      </h2>
      <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">
        Photograph the paper you already have. The medicines and quantities are
        read off it and written to this shelf.
      </p>

      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) pick(file);
        }}
      />
      <button
        onClick={() => input.current?.click()}
        disabled={busy}
        className="mt-2.5 min-h-11 w-full rounded-md border border-brand px-3 text-[13px] font-medium text-brand disabled:opacity-60"
      >
        {busy ? "Reading the photo…" : "Photograph a bill · बिल की फ़ोटो लें"}
      </button>

      {error && (
        <p role="alert" className="mt-2 text-[12px] leading-snug text-crit">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-3 border-t border-line pt-2.5">
          <p className="text-[12px] text-ink">
            <span className="font-medium">
              {result.committed} of {result.lines.length} line
              {result.lines.length === 1 ? "" : "s"} recorded
            </span>
            {result.document_date && (
              <span className="text-ink-2"> · dated {result.document_date}</span>
            )}
          </p>
          <p className="mt-0.5 text-[11px] leading-snug text-ink-3">
            {result.ai ? (
              <>
                Read by {result.model} · confidence{" "}
                {(result.confidence * 100).toFixed(0)}% · {result.verification}
              </>
            ) : (
              // No model ran. Saying so plainly, rather than showing a
              // confidence figure that nothing computed.
              <>Mock extraction — no model was called, so nothing is verified.</>
            )}
          </p>
          {result.notes && (
            <p className="mt-1 text-[11px] leading-snug text-ink-3">{result.notes}</p>
          )}

          <ul className="mt-2 flex flex-col gap-1.5">
            {result.lines.map((l, i) => (
              <li key={`${l.medicine}-${i}`} className="text-[12px] leading-snug">
                <span className={l.committed ? "text-ink" : "text-ink-3"}>
                  <span className="font-mono">
                    {Math.round(l.quantity).toLocaleString("en-IN")}
                  </span>{" "}
                  {l.sku_name ?? l.medicine}
                </span>
                {l.committed ? (
                  <span className="ml-1.5 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ok">
                    recorded
                  </span>
                ) : (
                  <>
                    <span className="ml-1.5 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-risk">
                      not recorded
                    </span>
                    {l.reason && (
                      <span className="block text-[11px] text-ink-3">{l.reason}</span>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

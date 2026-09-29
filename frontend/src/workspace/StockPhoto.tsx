/**
 * Photograph a stock document, and the shelf changes the way it means.
 *
 * A delivery slip, an issue register page and a stock count are the papers a
 * pharmacist already has in hand; typing them into a form is the step that
 * gets skipped at the end of a long shift. So the camera does it, and the
 * same Gemini pipeline that reads a ward photo reads this one.
 *
 * The same number means three things on three documents, so the model first
 * says which document it is: a delivery is added (through its dispatch record,
 * so it cannot count twice), an issue is subtracted, a count is set. Every line
 * is shown with what it did to the shelf, before and after, and every line
 * that was not applied says why — an invented or misread quantity on a stock
 * ledger is worse than no reading, because the reorder threshold, the forecast
 * and the redistribution solver all read it next.
 *
 * The photograph itself is never uploaded anywhere but here, and never stored.
 * Only what was read from it is.
 */

import { useRef, useState } from "react";

import { ApiError, type StockDocumentType, type StockPhotoLine, type StockPhotoResult, api } from "../api";

const DOCUMENT_LABEL: Record<StockDocumentType, string> = {
  delivery_slip: "Delivery slip · डिलीवरी पर्ची",
  issue_record: "Issue record · निर्गम रजिस्टर",
  stock_count: "Stock count · स्टॉक गिनती",
  unknown: "Document type not recognised",
};

const ACTION: Record<StockPhotoLine["action"], { label: string; tone: string }> = {
  added: { label: "added", tone: "text-ok" },
  subtracted: { label: "subtracted", tone: "text-ok" },
  set: { label: "count set", tone: "text-ok" },
  not_applied: { label: "not applied", tone: "text-risk" },
};

const n = (x: number) => Math.round(x).toLocaleString("en-IN");

function age(days: number | null): string {
  if (days === null) return "";
  if (days <= 0) return " (today)";
  return days === 1 ? " (1 day old)" : ` (${days} days old)`;
}

/** Longest edge sent to the model, in pixels. */
const MAX_EDGE = 1600;
const JPEG_QUALITY = 0.82;

/**
 * Shrink the photograph before it leaves the handset.
 *
 * A phone camera writes three or four megabytes, base64 adds a third again,
 * and the centres this screen is for are the ones on a 2G tail. Measured on a
 * delivery note on 2026-09-23: the model read a 1280px copy exactly as
 * accurately as the full-size one and answered in a sixth of the time, so the
 * extra pixels buy nothing but upload seconds.
 *
 * Falls back to the original bytes whenever the canvas path is unavailable —
 * a slow upload is a worse demo than no upload, but a failed one is worse
 * still.
 */
async function forUpload(file: File): Promise<{ base64: string; mime: string }> {
  const raw = () =>
    new Promise<{ base64: string; mime: string }>((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Could not read that file"));
      // readAsDataURL gives "data:image/jpeg;base64,XXXX"; the server wants
      // only the payload.
      reader.onload = () =>
        resolve({
          base64: String(reader.result).split(",")[1] ?? "",
          mime: file.type || "image/jpeg",
        });
      reader.readAsDataURL(file);
    });

  if (typeof createImageBitmap !== "function") return raw();
  try {
    const bitmap = await createImageBitmap(file);
    const longest = Math.max(bitmap.width, bitmap.height);
    const scale = Math.min(1, MAX_EDGE / longest);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return raw();
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();
    const url = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
    const base64 = url.split(",")[1] ?? "";
    return base64 ? { base64, mime: "image/jpeg" } : raw();
  } catch {
    return raw();
  }
}

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
      const { base64, mime } = await forUpload(file);
      const r = await api.stockPhoto(facilityId, {
        image_base64: base64,
        image_mime: mime,
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
      aria-label="Update stock from a photographed document"
      className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
    >
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
        From a delivery slip, issue register or stock count
      </h2>
      <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">
        Photograph the paper you already have. A delivery is added through its
        dispatch record, an issue is subtracted, a count replaces the figure.
        Anything doubtful is not applied, and says why.
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
        {busy ? "Reading the photo…" : "Photograph a document · दस्तावेज़ की फ़ोटो लें"}
      </button>

      {error && (
        <p role="alert" className="mt-2 text-[12px] leading-snug text-crit">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-3 border-t border-line pt-2.5">
          <p className="text-[12px] text-ink">
            <span className="font-medium">{DOCUMENT_LABEL[result.document_type]}</span>
            <span className="text-ink-2">
              {" "}· {result.committed} of {result.lines.length} line
              {result.lines.length === 1 ? "" : "s"} applied
            </span>
            {result.document_date && (
              <span className="text-ink-2">
                {" "}· dated {result.document_date}
                {age(result.document_age_days)}
              </span>
            )}
          </p>
          <p className="mt-0.5 text-[11px] leading-snug text-ink-3">
            {result.ai ? (
              <>
                Read by Gemini ({result.model}). Its own estimate of how well it read
                the page: {(result.confidence * 100).toFixed(0)}% — an estimate, not a check.
              </>
            ) : (
              // No model ran. Saying so plainly, rather than showing a
              // confidence figure that nothing computed.
              <>Test reader — no model was called, so nothing here was read from your photo.</>
            )}
          </p>
          {result.notes && (
            <p className="mt-1 text-[11px] leading-snug text-ink-3">{result.notes}</p>
          )}

          <ul className="mt-2 flex flex-col gap-1.5">
            {result.lines.map((l, i) => (
              <li key={`${l.medicine}-${i}`} className="text-[12px] leading-snug">
                <span className={l.committed ? "text-ink" : "text-ink-3"}>
                  <span className="font-mono">{n(l.quantity)}</span>
                  {l.unit ? ` ${l.unit}` : ""} {l.sku_name ?? l.medicine}
                </span>
                <span
                  className={`ml-1.5 text-[10.5px] font-semibold uppercase tracking-[0.06em] ${ACTION[l.action].tone}`}
                >
                  {ACTION[l.action].label}
                </span>
                {l.committed && l.qty_after !== null ? (
                  <span className="block text-[11px] text-ink-2">
                    Shelf:{" "}
                    <span className="font-mono">
                      {l.qty_before == null ? "no earlier figure" : n(l.qty_before)}
                      {" → "}
                      {n(l.qty_after)}
                    </span>
                    {l.action === "added"
                      ? " · delivery confirmed against its dispatch record"
                      : ""}
                  </span>
                ) : (
                  l.reason && <span className="block text-[11px] text-ink-3">{l.reason}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

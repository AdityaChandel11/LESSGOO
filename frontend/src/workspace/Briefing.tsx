/**
 * "What should I do today?"
 *
 * The line on screen when this mounts is computed from the same figures as the
 * cards below it. It needs no key, no network and no quota, and it is what a
 * pharmacist reads on every visit. That is deliberate: a screen whose first
 * sentence depends on a model is a screen that is blank when the model is out
 * of quota, and the free tier allows twenty generate requests a day.
 *
 * The model is an overlay on top of it, and only ever from a click. Never on
 * mount, never on a tab change, never on a poll. When the model answers, its
 * name goes on the line; when it cannot — no key, wrong mode, quota spent,
 * network gone — the computed line stays exactly where it was, with no AI
 * label and a quiet note saying why. The app never presents computed text as
 * a model's work, and never presents a failure as an answer.
 */

import { useState } from "react";

import { ApiError, type Briefing as BriefingReply, api } from "../api";

type Lang = "en" | "hi";

export default function Briefing({
  facilityId,
  computed,
}: {
  facilityId: string;
  /** Both languages of the computed line, from the workspace payload. */
  computed: Record<string, string> | undefined;
}) {
  const [lang, setLang] = useState<Lang>("en");
  const [reply, setReply] = useState<BriefingReply | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Generate requests this browser session has actually caused. A cached
  // answer is not a call and is not counted. This is not Google's daily
  // counter — nothing here can see that — so it says what it is.
  const [calls, setCalls] = useState(0);

  // A reply is only shown for the language it was asked in; switching language
  // drops back to the computed line rather than showing a mismatched sentence.
  const live = reply && reply.lang === lang && reply.ai ? reply : null;
  const body = live ? live.body : computed?.[lang];
  const note = reply && reply.lang === lang ? reply.note : null;

  // A server that predates this panel sends no computed line. That is a
  // deployment mismatch, not a pharmacist's problem: render nothing rather
  // than taking the whole workspace down with an undefined lookup. The
  // medicines below are what they came for and are unaffected.
  if (!body) return null;

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.briefing(facilityId, lang);
      setReply(r);
      if (r.ai && !r.cached) setCalls((n) => n + 1);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="What to do today"
      className="mb-3 rounded-lg border border-line bg-panel px-3.5 py-3"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-3">
          Today · आज
        </h2>
        <div role="group" aria-label="Language" className="flex shrink-0 gap-1">
          {(["en", "hi"] as Lang[]).map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              aria-pressed={lang === l}
              className={`min-h-8 rounded px-2 text-[11.5px] font-medium ${
                lang === l ? "bg-brand text-white" : "border border-line text-ink-2"
              }`}
            >
              {l === "en" ? "EN" : "हिं"}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink">{body}</p>

      {live && (
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
          Written by {live.model}
          {live.cached && " · from today's cached answer"}
        </p>
      )}

      {note && (
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">{note}</p>
      )}

      {error && (
        <p role="alert" className="mt-1.5 text-[11.5px] leading-snug text-crit">
          {error}
        </p>
      )}

      <div className="mt-2.5 flex items-center justify-between gap-3">
        <button
          onClick={ask}
          disabled={busy}
          className="min-h-11 rounded-md border border-brand px-3 text-[12.5px] font-medium text-brand disabled:opacity-60"
        >
          {busy ? "Asking…" : "Ask for today's summary · आज का सारांश"}
        </button>
        <span className="shrink-0 text-right text-[10.5px] leading-tight text-ink-3">
          {calls} model call{calls === 1 ? "" : "s"}
          <br />
          this session
        </span>
      </div>
    </section>
  );
}

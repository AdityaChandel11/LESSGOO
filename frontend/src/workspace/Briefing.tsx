/**
 * "What should I do today?"
 *
 * The list on screen when this mounts is computed from this centre's own rows
 * — counts that are overdue, shelves running out, deliveries due and late,
 * requests waiting, an outbreak in the district, today's ward report and
 * check-in — in an order a rule decides (fix #85). It needs no key, no network
 * and no quota, and it is what a pharmacist reads on every visit. That is
 * deliberate: a screen whose first lines depend on a model is a screen that is
 * blank when the model is out of quota, and the free tier allows twenty
 * generate requests a day.
 *
 * The model is an overlay on top of it, and only ever from a click. Never on
 * mount, never on a tab change, never on a poll. It rewrites the same list in
 * plainer words, in Hindi, and in the state's own language — which only it
 * can write. When it answers, its name goes on the list; when it cannot — no
 * key, wrong mode, quota spent, network gone, or a figure in its answer that
 * is not in the list — the computed list stays exactly where it was, with no
 * AI label and a quiet note saying why. The app never presents computed text
 * as a model's work, and never presents a failure as an answer.
 */

import { useState } from "react";

import { ApiError, type Briefing as BriefingReply, type TodoItem, api } from "../api";

const TAB_LABEL: Record<TodoItem["tab"], string> = {
  medicines: "Medicines · दवाएँ",
  orders: "Orders · ऑर्डर",
  beds: "Beds · बेड",
  attendance: "Attendance · उपस्थिति",
};

export default function Briefing({
  facilityId,
  computed,
  todo,
  localLanguage,
}: {
  facilityId: string;
  /** Both languages of the one computed line: what a server that predates
   *  the list sends, and the fallback when the list is absent. */
  computed: Record<string, string> | undefined;
  /** Today's computed list, most urgent first. */
  todo: TodoItem[] | undefined;
  /** The state's own language; the list in it is written only by the model. */
  localLanguage: { code: string; name: string; native: string } | null | undefined;
}) {
  const [lang, setLang] = useState("en");
  const [reply, setReply] = useState<BriefingReply | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Generate requests this browser session has actually caused. A cached
  // answer is not a call and is not counted. This is not Google's daily
  // counter — nothing here can see that — so it says what it is.
  const [calls, setCalls] = useState(0);

  const isLocal = !!localLanguage && lang === localLanguage.code;

  // A reply is only shown for the language it was asked in; switching language
  // drops back to the computed list rather than showing a mismatched one.
  const live = reply && reply.lang === lang && reply.ai ? reply : null;
  const note = reply && reply.lang === lang ? reply.note : null;

  // The computed list has no wording in the state's language, so that view
  // shows the English lines until the model has written its own.
  const rules: { text: string; tab: TodoItem["tab"] | null }[] = todo?.length
    ? todo.map((t) => ({ text: lang === "hi" ? t.hi : t.en, tab: t.tab }))
    : computed?.[lang === "hi" ? "hi" : "en"]
      ? [{ text: computed[lang === "hi" ? "hi" : "en"], tab: null }]
      : [];
  const lines = live ? live.lines.map((text) => ({ text, tab: null })) : rules;

  // A server that predates this panel sends nothing to show. That is a
  // deployment mismatch, not a pharmacist's problem: render nothing rather
  // than taking the whole workspace down. The medicines below are what they
  // came for and are unaffected.
  if (lines.length === 0) return null;

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

  const languages = [
    { code: "en", label: "EN", name: "English" },
    { code: "hi", label: "हिं", name: "Hindi" },
    ...(localLanguage
      ? [{ code: localLanguage.code, label: localLanguage.native, name: localLanguage.name }]
      : []),
  ];

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
          {languages.map((l) => (
            <button
              key={l.code}
              onClick={() => setLang(l.code)}
              aria-pressed={lang === l.code}
              aria-label={l.name}
              lang={l.code}
              className={`min-h-8 rounded px-2 text-[11.5px] font-medium ${
                lang === l.code ? "bg-brand text-white" : "border border-line text-ink-2"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>

      <ol lang={live || lang === "hi" ? lang : "en"} className="mt-1.5 space-y-1.5">
        {lines.map((line, i) => (
          <li key={`${i}-${line.text}`} className="flex gap-2 text-[13.5px] leading-snug text-ink">
            <span aria-hidden="true" className="w-4 shrink-0 text-right font-mono text-[12px] text-ink-3">
              {i + 1}
            </span>
            <span>
              {line.text}
              {line.tab && (
                <span lang="en" className="ml-1.5 whitespace-nowrap text-[11px] text-ink-3">
                  {TAB_LABEL[line.tab]}
                </span>
              )}
            </span>
          </li>
        ))}
      </ol>

      {isLocal && !live && !note && localLanguage && (
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
          The {localLanguage.name} list is written by Gemini. Until you ask for it, this is the
          computed list in English.
        </p>
      )}

      {live && (
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
          Written by {live.model} from the computed list; any figure not in that list is refused
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
          {busy ? "Asking…" : "Ask Gemini to write today's list · आज की सूची"}
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

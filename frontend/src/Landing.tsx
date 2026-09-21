import { useEffect, useState } from "react";

import BrandMark from "./Brand";
import DataNotice from "./DataNotice";
import LandingMap from "./LandingMap";
import {
  ApiError,
  type Bucket,
  type DemoAccount,
  ROLE_LABEL,
  type Session,
  STATUS_COLOR,
  STATUS_LABEL,
  type Status,
  type Summary,
  api,
  auth,
} from "./api";

/**
 * The public front door.
 *
 * A judge, an officer or a stranger arrives here with no session. The page has
 * to answer three questions before they decide whether to click anything: what
 * is the problem, what does this do about it, and is any of it real.
 *
 * The third question is why the hero is the live national map and why every
 * figure in the prose is read from /api/map/summary on load. Nothing here is
 * typed in by hand — if the database were reseeded tomorrow the sentences
 * would change with it, and if the API were down the numbers would be absent
 * rather than stale.
 */

const STEPS = [
  {
    title: "A phone reports stock",
    hindi: "फ़ोन से स्टॉक की सूचना",
    body:
      "A health worker sends the day's count from whatever they have — an SMS, a voice call, " +
      "or the app in a browser. Every channel normalises into the same reading, so a 2G " +
      "handset in a village reaches the network the same way a district office does.",
  },
  {
    title: "A shared model forecasts",
    hindi: "साझा मॉडल पूर्वानुमान लगाता है",
    body:
      "Each state trains on its own rows and sends back model weights only — never a facility " +
      "record, never a row of stock. The national model those weights build predicts when each " +
      "shelf runs out, and the warning fires while there is still time to act on it.",
  },
  {
    title: "A human approves the transfer",
    hindi: "मंज़ूरी इंसान देता है",
    body:
      "The optimiser proposes a route from a district holding surplus to one running short, " +
      "with the reasoning and the road distance attached. An officer approves or rejects it. " +
      "Nothing moves on its own, and no proposal takes a donor below its own safety stock.",
  },
] as const;

const fmt = (n: number) => n.toLocaleString("en-IN");

export default function Landing({
  onSignedIn,
  onSignIn,
}: {
  onSignedIn: (s: Session) => void;
  onSignIn: () => void;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [states, setStates] = useState<Bucket[]>([]);
  const [demoAccount, setDemoAccount] = useState<DemoAccount | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // Both are public (see public_router in api.py): the page has to render for
    // somebody who has never signed in.
    Promise.all([api.summary(null), api.states(null)])
      .then(([s, st]) => {
        if (!alive) return;
        setSummary(s);
        setStates(st);
      })
      .catch(() => undefined);
    // 404s when the deployment is not in demo mode, which simply means there is
    // no demo to offer and sign-in becomes the only way in.
    auth
      .demoAccounts()
      .then((list) => {
        if (alive) setDemoAccount(list[0] ?? null);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const enterDemo = async () => {
    if (!demoAccount) return;
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await auth.demoLogin(demoAccount.email));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open the demo.");
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full bg-canvas font-sans text-ink">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3.5">
          <div className="flex items-center gap-3">
            <BrandMark size={30} />
            <div>
              <div className="text-[15px] font-semibold tracking-tight">SwasthSetu</div>
              <div className="text-[11.5px] text-ink-3">National Health Supply Command</div>
            </div>
          </div>
          <button
            type="button"
            onClick={onSignIn}
            className="rounded-sm text-[13px] font-medium text-brand underline underline-offset-4 hover:text-ink focus:ring-2 focus:ring-brand/30 focus:outline-none"
          >
            Sign in
          </button>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl gap-10 px-5 py-12 lg:grid-cols-2 lg:items-center lg:gap-14 lg:py-16">
          <div>
            <p className="text-[11.5px] font-medium tracking-[0.14em] text-ink-3 uppercase">
              राष्ट्रीय स्वास्थ्य आपूर्ति कमान
            </p>
            <h1 className="mt-3 text-[32px] leading-[1.14] font-semibold tracking-tight lg:text-[40px]">
              Medicine on the shelf, before the patient arrives.
            </h1>
            <p className="mt-5 max-w-xl text-[15.5px] leading-relaxed text-ink-2">
              SwasthSetu watches medicine stock, hospital beds and staff attendance across{" "}
              {summary ? (
                <>
                  {fmt(summary.facilities)} primary and community health centres in{" "}
                  {fmt(summary.states)} states and union territories
                </>
              ) : (
                <>India&rsquo;s primary and community health centres</>
              )}{" "}
              — and plans the transfer before a shelf runs empty.
            </p>

            {summary && (
              <p className="mt-6 border-l-2 border-crit pl-3.5 text-[14px] leading-relaxed text-ink-2">
                <span className="font-medium text-ink">{fmt(summary.critical)} facilities</span> are
                below their reorder floor right now, across {fmt(summary.districts)} districts. Every
                figure on this page is read from the live database as it loads — none of them are
                written into the page.
              </p>
            )}

            <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3">
              <button
                type="button"
                onClick={demoAccount ? enterDemo : onSignIn}
                disabled={busy}
                className="h-11 rounded-md bg-brand px-6 text-[14.5px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:ring-offset-2 focus:outline-none disabled:opacity-55"
              >
                {demoAccount ? (busy ? "Opening the demo…" : "Try the demo") : "Sign in"}
              </button>
              {demoAccount && (
                <span className="text-[12.5px] text-ink-3">
                  No sign-up. Opens as {ROLE_LABEL[demoAccount.role]} · {demoAccount.scope}.
                </span>
              )}
            </div>
            {error && (
              <p role="alert" className="mt-3 text-[12.5px] text-crit">
                {error}
              </p>
            )}
          </div>

          <div>
            <LandingMap states={states} />
            <ul className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[12px] text-ink-2">
              {(["critical", "at_risk", "healthy"] as Status[]).map((s) => (
                <li key={s} className="flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: STATUS_COLOR[s] }}
                  />
                  {STATUS_LABEL[s]}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11.5px] leading-snug text-ink-3">
              One dot per state and union territory, sized by how many health centres it holds and
              coloured by the share of them short of stock. Sign in to zoom to a district, and then
              to a single facility.
            </p>
          </div>
        </section>

        <section aria-labelledby="how-it-works" className="border-t border-line bg-panel">
          <div className="mx-auto max-w-6xl px-5 py-12 lg:py-14">
            <h2
              id="how-it-works"
              className="text-[12px] font-semibold tracking-[0.14em] text-ink-3 uppercase"
            >
              How it works
            </h2>
            <ol className="mt-6">
              {STEPS.map((step, i) => (
                <li
                  key={step.title}
                  className="grid gap-x-6 gap-y-2 border-t border-line py-7 sm:grid-cols-[2.5rem_minmax(0,15rem)_minmax(0,1fr)]"
                >
                  <span aria-hidden="true" className="font-mono text-[13px] text-ink-3">
                    0{i + 1}
                  </span>
                  <h3 className="text-[16.5px] leading-snug font-semibold tracking-tight">
                    {step.title}
                    <span className="mt-0.5 block text-[13px] font-normal text-ink-3">
                      {step.hindi}
                    </span>
                  </h3>
                  <p className="text-[14.5px] leading-relaxed text-ink-2">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-5 py-8 sm:flex-row sm:items-start sm:justify-between">
          <DataNotice className="max-w-2xl" />
          <button
            type="button"
            onClick={onSignIn}
            className="shrink-0 rounded-sm text-left text-[12.5px] font-medium text-brand underline underline-offset-4 hover:text-ink focus:ring-2 focus:ring-brand/30 focus:outline-none"
          >
            Sign in with a department account
          </button>
        </div>
      </footer>
    </div>
  );
}

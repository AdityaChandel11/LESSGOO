/**
 * The public front door.
 *
 * A judge, an officer or a stranger arrives here with no session, and the page
 * has to answer three questions before they decide whether to click anything:
 * what is the problem, what does this do about it, and is any of it real.
 *
 * The shape answers the first two in the fold — a claim on the left, a way in
 * on the right — and the third below it, because "is this real" is not a
 * question a headline can settle. It is settled by the figures in the navy
 * panel and the map further down, every one of which is read from
 * /api/map/summary and /api/map/states as the page loads. Nothing here is
 * typed in by hand: if the database were reseeded tomorrow the numbers would
 * change with it, and if the API were down they would be absent rather than
 * stale. A front door that quotes yesterday's numbers is a front door that
 * has already told its first lie.
 *
 * The way in sits in the fold rather than below a scroll, because the single
 * most valuable thing a visitor can do here is stop reading and go look at
 * the thing itself.
 */

import { useEffect, useState } from "react";

import BrandMark from "./Brand";
import DataNotice from "./DataNotice";
import LandingMap from "./LandingMap";
import SignInPanel from "./SignInPanel";
import {
  type Bucket,
  STATUS_COLOR,
  STATUS_LABEL,
  type Session,
  type Status,
  type Summary,
  api,
} from "./api";

const STEPS = [
  {
    n: "01",
    title: "A phone reports stock",
    hindi: "फ़ोन से स्टॉक की सूचना",
    body:
      "A health worker sends the day's count from whatever they have — an SMS, a voice call, " +
      "a photograph of the delivery note, or the app in a browser. Every channel normalises " +
      "into the same reading, so a 2G handset in a village reaches the network the same way a " +
      "district office does.",
  },
  {
    n: "02",
    title: "Four states train a shared forecasting model today",
    hindi: "आज चार राज्य मिलकर एक पूर्वानुमान मॉडल प्रशिक्षित करते हैं",
    body:
      "Maharashtra, Kerala, Bihar and Uttar Pradesh each train on their own rows and send back " +
      "model weights only — never a facility record, never a row of stock. Where no fresh " +
      "forecast exists, days of stock come from the last 28 days of readings instead, and the " +
      "screen says which of the two it used.",
  },
  {
    n: "03",
    title: "The donor centre accepts; an officer handles exceptions",
    hindi: "दाता केंद्र स्वीकार करता है; अपवाद अधिकारी सँभालते हैं",
    body:
      "The optimiser proposes a route from a centre holding surplus to one running short, with " +
      "the reasoning and the distance attached. The centre that would give the stock accepts " +
      "or declines; officers see the whole picture and step in where something is stuck. " +
      "Nothing moves on its own, and no proposal takes a donor below its own safety stock.",
  },
] as const;

/**
 * What runs live, what is recorded, what is next — answered before a judge
 * has to ask. The deployed database is a small free instance: anything that
 * writes a handful of rows per action runs live; anything heavy (training,
 * a reseed) was run once and its proof is stored; the rest is roadmap and is
 * called that. Update a row when the thing it describes moves.
 */
const RUNS = [
  {
    key: "live",
    title: "Live on this site",
    hindi: "इस साइट पर लाइव",
    items: [
      "Stock counts, bill photos read by Gemini, requests between centres, dispatches and confirmed receipts",
      "Days-of-stock warnings, the redistribution solver and trust scores, computed from the database each time you look",
      "Ward bed reports read by Gemini, staff check-ins and the daily briefing",
    ],
    why: "Each action writes a handful of small rows.",
  },
  {
    key: "recorded",
    title: "Recorded, with proof",
    hindi: "रिकॉर्ड किया गया, प्रमाण सहित",
    items: [
      "Federated training: four state processes on a laptop. Every round's bytes and weights hash are stored and shown on the Federation tab",
      "The forecasts published from that run — used while fresh, otherwise the 28-day burn rate",
      "The synthetic dataset, and outbreak rows parsed from NCDC's weekly IDSP reports",
    ],
    why: "Heavy compute or bulk writes, run once and kept.",
  },
  {
    key: "next",
    title: "Next, said plainly",
    hindi: "आगे का काम",
    items: [
      "A real SMS, WhatsApp and voice carrier (needs DLT registration) — the demo uses a built-in handset simulator",
      "One database per state, instead of one database separated by state",
      "District warehouse stock and indents; differential privacy and secure aggregation",
    ],
    why: "Not built. Named here so nobody mistakes it for a feature.",
  },
] as const;

/**
 * The demo, for a judge alone on the site (fix list #36): five steps, two
 * centres in two windows, every step through the real endpoints. The first
 * step is the cold start — a free server that sleeps is the first thing a
 * visitor meets, so it is said before it is suffered.
 */
const DEMO_SCRIPT = [
  "Open this link a minute before you start: the free server sleeps when idle, and the first load can take up to a minute.",
  "Continue as Pharmacist, Nashik PHC 1. On Medicines, open a medicine running short, press Find supply and request stock (pick Nashik PHC 13 if it is offered).",
  "In a private window, continue as Pharmacist, Nashik PHC 13. Orders → Requests for your stock: accept and send, or decline. No request waiting? Simulate a request from a neighbour raises a real one.",
  "Back as PHC 1: the medicine card shows the reply, and Orders confirms the delivery when it arrives, counting what came.",
  "Continue as the Maharashtra state officer: Redistribution, Movements and Data trust show the same rows — who asked, who sent, what arrived.",
] as const;

function DemoScript() {
  return (
    <div className="mt-5 rounded-lg border border-line bg-canvas px-3.5 py-3">
      <h3 className="text-[12.5px] font-semibold text-ink">
        Try it in five steps <span lang="hi" className="font-normal text-ink-3">· पाँच चरणों में आज़माएँ</span>
      </h3>
      <ol className="mt-2 list-decimal space-y-1.5 pl-4 text-[12px] leading-snug text-ink-2">
        {DEMO_SCRIPT.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <a
        href="#live-recorded-next"
        className="mt-2.5 inline-block rounded-sm text-[12px] font-medium text-brand underline-offset-4 hover:underline focus:ring-2 focus:ring-brand/25 focus:outline-none"
      >
        What runs live, what's recorded, what's next ↓
      </a>
    </div>
  );
}

const fmt = (n: number) => n.toLocaleString("en-IN");

export default function Landing({
  onSignedIn,
  resume,
}: {
  onSignedIn: (s: Session) => void;
  /** Present only when a session is already open — see AuthGate. */
  resume?: { label: string; onResume: () => void };
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [states, setStates] = useState<Bucket[]>([]);

  useEffect(() => {
    let alive = true;
    // Both are public (see public_router in api.py): the page has to render
    // for somebody who has never signed in.
    Promise.all([api.summary(null), api.states(null)])
      .then(([s, st]) => {
        if (!alive) return;
        setSummary(s);
        setStates(st);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="min-h-full bg-canvas font-sans text-ink">
      {/* ------------------------------------------------------- the fold --- */}
      <section className="flex min-h-screen flex-col lg:flex-row">
        <div className="relative flex flex-col justify-between overflow-hidden bg-brand px-6 py-10 text-white sm:px-10 lg:w-[46%] lg:shrink-0">
          <div className="flex items-center gap-3">
            <BrandMark size={34} />
            <div>
              <div className="text-[17px] font-semibold tracking-tight">SwasthSetu</div>
              <div className="text-[12px] text-white/70">National Health Supply Command</div>
            </div>
          </div>

          <div className="relative z-10 max-w-md py-12 lg:py-0">
            <h1 className="text-[30px] leading-[1.15] font-semibold tracking-tight sm:text-[34px]">
              Medicine on the shelf, before the patient arrives.
            </h1>
            <p className="mt-4 text-[14.5px] leading-relaxed text-white/75">
              Live stock across primary and community health centres, transfers planned before
              shelves empty, and reports that reach the network from any phone.
            </p>

            {/* The proof, not a claim: read from the database as this page
                loaded. Absent rather than invented when the API cannot be
                reached, which is the only honest thing a number can do. */}
            {summary && (
              <div className="mt-8 border-t border-white/15 pt-6">
                <dl className="grid grid-cols-3 gap-4">
                  {[
                    [fmt(summary.facilities), "health centres"],
                    [fmt(summary.states), "states & UTs"],
                    [fmt(summary.districts), "districts"],
                  ].map(([value, label]) => (
                    <div key={label}>
                      <dt className="font-mono text-[22px] leading-none font-semibold tabular-nums">
                        {value}
                      </dt>
                      <dd className="mt-1.5 text-[11.5px] leading-snug text-white/65">{label}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-5 flex items-start gap-2.5 text-[12.5px] leading-snug text-white/75">
                  <span
                    aria-hidden="true"
                    className="mt-1 h-2 w-2 shrink-0 rounded-full"
                    style={{ background: STATUS_COLOR.critical }}
                  />
                  <span>
                    <span className="font-semibold text-white">
                      {fmt(summary.critical)} centres
                    </span>{" "}
                    are below their reorder floor, as of each centre's last report. Figures are
                    read from the live database as this page loads.
                    <span lang="hi" className="mt-1 block text-white/60">
                      आँकड़े लाइव डेटाबेस से; स्टॉक की स्थिति हर केंद्र की पिछली रिपोर्ट के अनुसार।
                    </span>
                  </span>
                </p>
              </div>
            )}
          </div>

          <p className="relative z-10 text-[11.5px] text-white/60">
            Prototype — not an official government system.{" "}
            <span lang="hi">प्रोटोटाइप — यह कोई आधिकारिक सरकारी प्रणाली नहीं है।</span>
          </p>

          <svg
            className="pointer-events-none absolute -right-24 -bottom-24 opacity-[0.08]"
            width="420"
            height="420"
            viewBox="0 0 420 420"
            aria-hidden="true"
          >
            {[200, 160, 120, 80].map((r) => (
              <circle key={r} cx="210" cy="210" r={r} fill="none" stroke="#fff" strokeWidth="18" />
            ))}
          </svg>
        </div>

        <main className="flex flex-1 flex-col items-center justify-center px-5 py-12 sm:px-8">
          {/* Only when a session is already open. The front door is the front
              door even for somebody who has been here before, but it must not
              become a wall they have to sign in through twice. */}
          {resume && (
            <div className="mb-7 w-full max-w-[400px] rounded-lg border border-brand/25 bg-brand/[0.04] px-3.5 py-3">
              <p className="text-[12.5px] text-ink-2">
                You are already signed in as{" "}
                <span className="font-medium text-ink">{resume.label}</span>.
              </p>
              <button
                type="button"
                onClick={resume.onResume}
                className="mt-2 h-10 w-full rounded-md bg-brand text-[13.5px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:ring-offset-2 focus:outline-none"
              >
                Continue →
              </button>
            </div>
          )}

          <SignInPanel onSignedIn={onSignedIn} showNotice={false} demoGuide={<DemoScript />} />

          <a
            href="#how-it-works"
            className="mt-10 rounded-sm text-[12.5px] font-medium text-ink-3 underline-offset-4 hover:text-brand hover:underline focus:ring-2 focus:ring-brand/25 focus:outline-none"
          >
            How it works ↓
          </a>
        </main>
      </section>

      {/* ---------------------------------------------------- how it works --- */}
      <section aria-labelledby="how-it-works" className="border-t border-line bg-panel">
        <div className="mx-auto max-w-6xl px-5 py-14 sm:px-8 lg:py-16">
          <h2
            id="how-it-works"
            className="scroll-mt-6 text-[12px] font-semibold tracking-[0.14em] text-ink-3 uppercase"
          >
            How it works
          </h2>
          <p className="mt-3 max-w-2xl text-[16px] leading-relaxed text-ink-2">
            Three steps, and a person at the end of them. The forecast is shared across states
            without the rows ever leaving one; the decision stays with people — the centre that
            gives the stock, and an officer for the exceptions.
          </p>

          <ol className="mt-10 grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3">
            {STEPS.map((step) => (
              <li key={step.n} className="flex flex-col bg-panel p-6">
                <span
                  aria-hidden="true"
                  className="font-mono text-[12px] tracking-wider text-brand"
                >
                  {step.n}
                </span>
                <h3 className="mt-4 text-[17px] leading-snug font-semibold tracking-tight">
                  {step.title}
                </h3>
                <span className="mt-1 block text-[13px] text-ink-3">{step.hindi}</span>
                <p className="mt-3.5 text-[14px] leading-relaxed text-ink-2">{step.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ------------------------------------------ live, recorded, next --- */}
      <section aria-labelledby="live-recorded-next" className="border-t border-line">
        <div className="mx-auto max-w-6xl px-5 py-14 sm:px-8 lg:py-16">
          <h2
            id="live-recorded-next"
            className="scroll-mt-6 text-[12px] font-semibold tracking-[0.14em] text-ink-3 uppercase"
          >
            What runs live, what's recorded, what's next
          </h2>
          <p lang="hi" className="mt-1 text-[13px] text-ink-3">
            क्या लाइव चलता है, क्या रिकॉर्ड है, आगे क्या है
          </p>
          <p className="mt-3 max-w-2xl text-[16px] leading-relaxed text-ink-2">
            The demo runs on a small free database. What costs a few rows per action runs live;
            what needs heavy compute ran once and its proof is kept; the rest is roadmap.
          </p>
          <div className="mt-8 grid gap-px overflow-hidden rounded-lg border border-line bg-line md:grid-cols-3">
            {RUNS.map((col) => (
              <div key={col.key} className="flex flex-col bg-panel p-6">
                <h3 className="text-[16px] leading-snug font-semibold tracking-tight">
                  {col.title}
                </h3>
                <span lang="hi" className="mt-0.5 block text-[13px] text-ink-3">
                  {col.hindi}
                </span>
                <ul className="mt-3.5 list-disc space-y-2 pl-4 text-[13.5px] leading-relaxed text-ink-2">
                  {col.items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <p className="mt-auto pt-4 text-[12.5px] text-ink-3">{col.why}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------- the evidence --- */}
      <section aria-labelledby="where" className="border-t border-line">
        <div className="mx-auto grid max-w-6xl gap-10 px-5 py-14 sm:px-8 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] lg:py-16">
          <div>
            <h2
              id="where"
              className="text-[12px] font-semibold tracking-[0.14em] text-ink-3 uppercase"
            >
              Where it is running
            </h2>
            <p className="mt-3 text-[16px] leading-relaxed text-ink-2">
              One dot per state and union territory, sized by how many health centres it holds and
              coloured by the share of them short of stock.
            </p>
            <ul className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[12.5px] text-ink-2">
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
            <p className="mt-4 text-[12.5px] leading-relaxed text-ink-3">
              Sign in to zoom to a district, and then to a single facility.
            </p>
          </div>
          <div>
            <LandingMap states={states} />
          </div>
        </div>
      </section>

      <footer className="border-t border-line bg-panel">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-5 py-8 sm:px-8">
          <DataNotice className="max-w-3xl" />
        </div>
      </footer>
    </div>
  );
}

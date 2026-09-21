/**
 * The cold start, made honest.
 *
 * This demo runs on a free Render instance, which is stopped after fifteen
 * minutes of inactivity and takes roughly a minute to come back — and the
 * container runs its migrations before it answers anything. A visitor who
 * arrives during that minute would otherwise watch a blank page and conclude
 * the site is broken. That is the most likely first impression a judge will
 * ever have of this project, so it gets a screen of its own.
 *
 * Nothing renders until `/api/health` answers, because the app underneath
 * fires its own requests immediately and they would hang for the same minute.
 * The waiting screen itself is held back for a moment: on a warm server the
 * probe returns in well under a second, and a panel that flashes up and
 * disappears is worse than no panel at all.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const HEALTH_URL = `${import.meta.env.VITE_API_ORIGIN ?? ""}/api/health`;

/** Long enough that a warm server never shows the waking screen. */
const QUIET_MS = 2_500;
/** A cold start is about 60s. Past this, say so rather than look stuck. */
const SLOW_AFTER_S = 75;
/** Total patience before offering the visitor the retry button. */
const GIVE_UP_MS = 150_000;
/** A sleeping instance refuses connections outright; that is not a failure. */
const RETRY_MS = 3_000;

type Phase = "probing" | "ready" | "unreachable";

function useElapsedSeconds(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    const id = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(id);
  }, [running]);
  return seconds;
}

function BrandMark() {
  return (
    <svg width="34" height="34" viewBox="0 0 26 26" aria-hidden="true">
      <rect width="26" height="26" rx="6" fill="#0b3d5c" />
      <path d="M13 6v14M6 13h14" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <circle cx="19.5" cy="6.5" r="3" fill="#e0900e" stroke="#0b3d5c" strokeWidth="1.5" />
    </svg>
  );
}

export default function BootGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("probing");
  const [visible, setVisible] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const startedAt = useRef(Date.now());
  const seconds = useElapsedSeconds(phase === "probing" && visible);

  const probe = useCallback(async (signal: AbortSignal): Promise<boolean> => {
    try {
      const res = await fetch(HEALTH_URL, { signal, credentials: "include" });
      return res.ok;
    } catch {
      // A stopped instance refuses the connection. Expected, so retry.
      return false;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    const quiet = window.setTimeout(() => {
      if (!cancelled) setVisible(true);
    }, QUIET_MS);

    (async () => {
      while (!cancelled) {
        if (await probe(controller.signal)) {
          if (!cancelled) setPhase("ready");
          return;
        }
        if (cancelled) return;
        if (Date.now() - startedAt.current > GIVE_UP_MS) {
          setVisible(true);
          setPhase("unreachable");
          return;
        }
        await new Promise((r) => window.setTimeout(r, RETRY_MS));
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(quiet);
    };
  }, [probe, attempt]);

  if (phase === "ready") return <>{children}</>;
  // Warm server: say nothing at all rather than flash a panel.
  if (!visible) return null;

  const retry = () => {
    startedAt.current = Date.now();
    setPhase("probing");
    setAttempt((n) => n + 1);
  };

  return (
    <div className="flex h-full items-center justify-center bg-canvas px-6 font-sans text-ink">
      <div className="w-full max-w-[26rem]" role="status" aria-live="polite">
        <div className="flex items-center gap-3">
          <BrandMark />
          <div className="leading-tight">
            <div className="text-[17px] font-semibold tracking-tight">SwasthSetu</div>
            <div className="text-[12px] text-ink-3">National Health Supply Command</div>
          </div>
        </div>

        {phase === "probing" ? (
          <>
            <h1 className="mt-7 flex items-center gap-2.5 text-[20px] font-semibold tracking-tight">
              <span
                aria-hidden="true"
                className="inline-block h-2.5 w-2.5 rounded-full bg-risk motion-safe:animate-pulse"
              />
              Waking the server
              <span className="text-[15px] font-normal text-ink-3">सर्वर चालू हो रहा है</span>
            </h1>
            <p className="mt-3 text-[13.5px] leading-relaxed text-ink-2">
              This demonstration runs on a free instance that stops when nobody is using it.
              The first visit takes about a minute while it starts up and applies its database
              migrations. Every page after this one is immediate.
            </p>
            <p className="mt-4 font-mono text-[12.5px] tabular-nums text-ink-3">
              {seconds}s elapsed
            </p>
            {seconds >= SLOW_AFTER_S && (
              <p className="mt-2 text-[12.5px] leading-relaxed text-ink-2">
                Still waking. A cold start occasionally takes up to two minutes.
              </p>
            )}
          </>
        ) : (
          <>
            <h1 className="mt-7 text-[20px] font-semibold tracking-tight">
              Could not reach the server
            </h1>
            <p className="mt-3 text-[13.5px] leading-relaxed text-ink-2">
              It did not answer within two and a half minutes. It may still be starting, or it
              may be down.
            </p>
            <button
              onClick={retry}
              className="mt-5 rounded-md bg-brand px-4 py-2 text-[13.5px] font-medium text-white hover:bg-brand/90 focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:outline-none"
            >
              Try again
            </button>
          </>
        )}

        <p className="mt-8 border-t border-line pt-4 text-[11.5px] leading-relaxed text-ink-3">
          Synthetic demonstration data. No real patient or facility records exist in this system.
        </p>
      </div>
    </div>
  );
}

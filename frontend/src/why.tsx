import { useState } from "react";
import { ApiError, type Explanation } from "./api";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "done"; answer: Explanation }
  | { kind: "error"; message: string };

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

/**
 * A "why" asked for by a click, never on load: the model's free tier is 20
 * requests a day. The Gemini label appears only when the server says the
 * sentence came from the model; the computed fallback is labelled as such.
 */
export function WhyLine({
  load,
  label = "Why?",
}: {
  load: () => Promise<Explanation>;
  label?: string;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });

  const ask = () => {
    setState({ kind: "loading" });
    load()
      .then((answer) => setState({ kind: "done", answer }))
      .catch((e) =>
        setState({
          kind: "error",
          message: e instanceof ApiError ? e.message : "Could not get an explanation",
        }),
      );
  };

  if (state.kind === "idle") {
    return (
      <button
        onClick={ask}
        className="mt-1.5 block text-left text-[11.5px] font-medium text-brand hover:underline"
      >
        {label}
      </button>
    );
  }
  if (state.kind === "loading") {
    return (
      <p className="mt-1.5 text-[11.5px] text-ink-3" role="status">
        Working it out…
      </p>
    );
  }
  if (state.kind === "error") {
    return <p className="mt-1.5 text-[11.5px] text-crit">{state.message}</p>;
  }

  const a = state.answer;
  return (
    <div className="mt-1.5 rounded-md border border-line bg-canvas px-2 py-1.5">
      <div className="flex items-center gap-1.5 text-[10.5px]">
        {a.ai ? (
          <span className="font-semibold text-brand" title={a.model ?? undefined}>
            <span aria-hidden="true">⚡</span> Gemini
          </span>
        ) : (
          <span className="font-semibold text-ink-2">Rule-based</span>
        )}
        {a.ai && a.latency_ms != null && (
          <span className="font-mono text-ink-3">
            {a.cached ? `cached · first answer took ${seconds(a.latency_ms)}` : seconds(a.latency_ms)}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[12px] leading-snug text-ink">{a.text}</p>
      {a.note && <p className="mt-0.5 text-[10.5px] text-ink-3">{a.note}</p>}
    </div>
  );
}

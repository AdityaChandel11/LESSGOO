/**
 * The silo inspector — spec 12.2 and 27, rewritten for fix #1.
 *
 * Two claims sit side by side here. The first is ordinary: a shared model
 * beats the burn-rate rule the dashboard uses today. The second is the one
 * that matters, and the one an accuracy chart can never make on its own —
 * that nothing but model weights ever left a state.
 *
 * So the evidence is shown, not summarised: the measured bytes, every tensor's
 * shape, the hash of the weights, and a facility-record count the aggregator
 * asserts before each round is written. The zero below is a result. If a silo
 * ever returned anything but weights and scalar numbers, the round would have
 * stopped instead of arriving here.
 *
 * Every figure on this panel is read from the recorded rounds or computed
 * from them on the server (api.federation_summary). Nothing is typed in, and
 * the two label sets — plain and technical — name the same fields.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  type FederationInspector,
  type FederationRound,
  type LiveRoundState,
} from "./api";

const LIVE_POLL_MS = 1000;

type Voice = "official" | "technical";

/** The same fields, said two ways. No label here names anything the
 *  recorded rounds do not hold. */
const LABELS: Record<Voice, Record<string, string>> = {
  official: {
    error: "Average forecast error",
    baseline: "Today's rule (the last 28 days' use), same test weeks",
    untrained: "Before training",
    best: "Best round",
    windows: "Training examples",
    trust: "Delivery-confirmation score",
    countsAs: "Weight in the shared model",
    weights: "Model data sent, per state per round",
    total: "Sent by all states across the run",
    hash: "Fingerprint of the model, last round",
    records: "Facility records sent",
    mae: "Error",
  },
  technical: {
    error: "Model error (MAE on the ratio)",
    baseline: "Burn-rate rule, same held-out weeks",
    untrained: "Before training (round 0)",
    best: "Best round",
    windows: "Windows",
    trust: "Trust (receipt discipline)",
    countsAs: "Counts as",
    weights: "Weights uploaded, per silo per round",
    total: "Uploaded by all silos across the run",
    hash: "Weights hash (SHA-256), last round",
    records: "Facility records sent",
    mae: "MAE",
  },
};

function mae(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

/** The model predicts next week's daily use as a ratio of the last four
 *  weeks', so its error is a share of a centre's usual daily use. */
function shareOfUse(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}% of a centre's usual daily use` : "—";
}

function bytes(value: number | null | undefined): string {
  if (typeof value !== "number") return "—";
  return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`;
}

/** Exact, because a rounded figure is what makes a measured one look typed in. */
function exactBytes(value: number | null | undefined): string {
  return typeof value === "number" ? `${value.toLocaleString("en-IN")} B` : "—";
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * The accuracy curve, drawn against the rule it has to beat.
 *
 * The scale covers the training rounds and the burn-rate line. The untrained
 * model (round 0) is about ten times worse than either, and a scale that
 * includes it flattens every round that matters into one line along the
 * bottom — so it is named beside the chart instead of drawn on it.
 *
 * `upTo` stops the line at a round so a replay can draw it one round at a
 * time. The axes never move while it does.
 */
function Curve({
  rounds,
  baseline,
  upTo,
  beatsFrom,
}: {
  rounds: FederationRound[];
  baseline: number | null;
  upTo?: number | null;
  beatsFrom: number | null;
}) {
  const trained = rounds.filter((r) => r.round_no >= 1 && r.global_val_mae != null);
  if (trained.length < 2) return null;
  const values = trained.map((r) => r.global_val_mae as number);
  const top = Math.max(...values, baseline ?? 0) * 1.12;
  const floor = Math.min(...values, baseline ?? Infinity) * 0.85;
  const w = 100;
  const h = 44;
  const x = (i: number) => (i / (trained.length - 1)) * w;
  const y = (v: number) => h - ((v - floor) / (top - floor)) * h;
  const reached = upTo == null ? trained.length : trained.filter((r) => rounds.indexOf(r) <= upTo).length;
  const shown = values.slice(0, reached);
  const path = shown
    .map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(v).toFixed(2)}`)
    .join(" ");
  const head = shown.length - 1;
  const beatsAt = beatsFrom == null ? -1 : trained.findIndex((r) => r.round_no === beatsFrom);

  return (
    <figure className="mt-2">
      <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-full" preserveAspectRatio="none" role="img"
        aria-label={`Model error across ${shown.length} of ${trained.length} training rounds${
          head >= 0 ? `, at ${mae(shown[head])}` : ""
        }; the burn-rate rule is ${mae(baseline)}`}>
        {baseline != null && (
          <line x1="0" x2={w} y1={y(baseline)} y2={y(baseline)} stroke="currentColor"
            className="text-ink-2" strokeWidth="0.8" strokeDasharray="3 2" vectorEffect="non-scaling-stroke" />
        )}
        {beatsAt >= 0 && beatsAt < shown.length && (
          <line x1={x(beatsAt)} x2={x(beatsAt)} y1="0" y2={h} stroke="currentColor"
            className="text-line" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
        {shown.length > 1 && (
          <path d={path} fill="none" stroke="currentColor" className="text-brand" strokeWidth="1.6"
            vectorEffect="non-scaling-stroke" />
        )}
        {head >= 0 && (
          <circle cx={x(head)} cy={y(shown[head])} r="1.6" className="fill-brand" />
        )}
      </svg>
      <figcaption className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10.5px] text-ink-3">
        <span className="flex items-center gap-1">
          <svg aria-hidden="true" width="14" height="4"><line x1="1" x2="13" y1="2" y2="2" stroke="currentColor" className="text-brand" strokeWidth="2" /></svg>
          shared model, rounds {trained[0].round_no}–{trained[trained.length - 1].round_no}
        </span>
        <span className="flex items-center gap-1">
          <svg aria-hidden="true" width="14" height="4"><line x1="1" x2="13" y1="2" y2="2" stroke="currentColor" className="text-ink-2" strokeWidth="2" strokeDasharray="4 3" /></svg>
          burn-rate rule {mae(baseline)}
        </span>
        {beatsFrom != null && <span>beats the burn-rate rule from round {beatsFrom}</span>}
      </figcaption>
    </figure>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11.5px] text-ink-2">{label}</span>
      <span className={`shrink-0 text-right font-mono tabular-nums ${strong ? "text-[13px] font-semibold text-ink" : "text-[11.5px] text-ink-2"}`}>
        {value}
      </span>
    </div>
  );
}

/**
 * Four state boxes and one aggregator, drawn flat. The label on the arrows is
 * the measured size of what each state sent; nothing moves on screen, because
 * nothing is moving — the run below is a recorded one.
 */
function Topology({ states, perRound }: { states: string[]; perRound: string }) {
  if (states.length === 0) return null;
  const rowH = 22;
  const h = Math.max(states.length * rowH, 60);
  return (
    <svg viewBox={`0 0 300 ${h}`} className="mt-2 w-full" role="img"
      aria-label={`${states.length} states each send ${perRound} of model weights per round to one aggregator; no facility records are sent`}>
      {states.map((s, i) => {
        const cy = i * rowH + rowH / 2;
        return (
          <g key={s}>
            <rect x="1" y={cy - 8} width="104" height="16" rx="2" className="fill-canvas stroke-line" strokeWidth="1" />
            <text x="53" y={cy + 3.5} textAnchor="middle" className="fill-ink" fontSize="9">{s}</text>
            <line x1="106" y1={cy} x2="206" y2={h / 2} className="stroke-ink-3" strokeWidth="0.8" />
          </g>
        );
      })}
      <rect x="208" y={h / 2 - 14} width="91" height="28" rx="2" className="fill-brand" />
      <text x="253.5" y={h / 2 + 3.5} textAnchor="middle" fill="#ffffff" fontSize="10" fontWeight="600">Aggregator</text>
      <text x="156" y="9" textAnchor="middle" className="fill-ink-2" fontSize="8">{perRound} of weights per round, each</text>
      <text x="156" y={h - 3} textAnchor="middle" className="fill-ink-2" fontSize="8">0 facility records</text>
    </svg>
  );
}

/** One round every this long. Slow enough to read a row, short enough that
 *  nine rounds do not outlast anybody's patience. */
const REPLAY_STEP_MS = 850;

/** Shared, because `?? []` would hand the silo effect a new array on every
 *  render and that effect sets state in the parent: a fresh reference each
 *  time is a render loop, not a no-op. */
const NO_ROUNDS: FederationRound[] = [];

/**
 * Which silo the weighting cost the most, stated from the row rather than
 * written in. A silo contributes its window count scaled by its receipt
 * discipline, so the gap between the two is the weighting made visible.
 */
function mostDownWeighted(silos: FederationRound["per_silo"]) {
  const scored = silos.filter((s) => s.windows > 0);
  if (scored.length < 2) return null;
  const worst = scored.reduce((a, b) => (a.trust <= b.trust ? a : b));
  const best = scored.reduce((a, b) => (a.trust >= b.trust ? a : b));
  if (worst.state === best.state || worst.trust >= best.trust) return null;
  return { worst, best, lostPct: Math.round((1 - worst.counts_as / worst.windows) * 100) };
}

export function FederationPanel({
  refreshKey,
  onSilos,
  stateName,
  canTrain = false,
  onOpenLedger,
}: {
  refreshKey: number;
  /** Reports the silo states upward so the map can ring them. */
  onSilos?: (states: string[]) => void;
  /** The rounds store two-letter codes; a reader wants the state. */
  stateName?: (code: string) => string;
  /** Whether this account may start a real round (administrators). */
  canTrain?: boolean;
  /** Open one state's movement ledger: the rows the weighting is computed from. */
  onOpenLedger?: (state: string) => void;
}) {
  const named = (code: string) => stateName?.(code) ?? code;
  const [data, setData] = useState<FederationInspector | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [voice, setVoice] = useState<Voice>("official");
  const L = LABELS[voice];
  // null: the finished run, as recorded. A number: the round a replay has
  // reached. The replay re-trains nothing — the rows are already written.
  const [replayAt, setReplayAt] = useState<number | null>(null);
  const timer = useRef<number | null>(null);
  const [live, setLive] = useState<LiveRoundState | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [freshRound, setFreshRound] = useState<number | null>(null);
  const livePoll = useRef<number | null>(null);

  const loadInspector = useCallback(
    () =>
      api
        .federationInspector()
        .then(setData)
        .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the inspector")),
    [],
  );

  useEffect(() => {
    setLoading(true);
    void loadInspector().finally(() => setLoading(false));
  }, [refreshKey, loadInspector]);

  const stopLivePoll = useCallback(() => {
    if (livePoll.current) window.clearInterval(livePoll.current);
    livePoll.current = null;
  }, []);
  useEffect(() => stopLivePoll, [stopLivePoll]);

  // Follow a running round until it settles. Used after a press and also when
  // the tab is opened while a round is already running — a round keeps
  // training on the server whether or not this panel is on screen.
  const followRound = useCallback(() => {
    if (livePoll.current) return;
    livePoll.current = window.setInterval(async () => {
      try {
        const state = await api.federationLive();
        setLive(state);
        if (state.job && state.job.status !== "running") {
          stopLivePoll();
          if (state.job.status === "done") {
            await loadInspector();
            setReplayAt(null);
            setFreshRound(state.job.round_no);
          }
        }
      } catch {
        // One missed poll is not a failed round; the next tick asks again.
      }
    }, LIVE_POLL_MS);
  }, [loadInspector, stopLivePoll]);

  useEffect(() => {
    if (!canTrain) return;
    api
      .federationLive()
      .then((state) => {
        setLive(state);
        if (state.job?.status === "running") followRound();
      })
      .catch(() => setLive(null));
  }, [canTrain, refreshKey, followRound]);

  // A real round: the server starts `flwr run`, and each phase shown below is
  // a line the aggregator printed. The new row comes from the database once
  // the round has written it, never from this component.
  const runNextRound = async () => {
    setLiveError(null);
    setFreshRound(null);
    try {
      const job = await api.startFederationRound();
      setLive((l) => ({ available: true, reason: null, ...(l ?? {}), job }));
    } catch (e) {
      setLiveError(e instanceof ApiError ? e.message : "Could not start the round");
      return;
    }
    stopLivePoll();
    followRound();
  };

  const job = live?.job ?? null;
  const training = job?.status === "running";

  // Bring the round that just landed into view: it is the thing to watch.
  useEffect(() => {
    if (freshRound == null) return;
    document
      .querySelector(`[data-round="${freshRound}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [freshRound, data]);

  const rounds = data?.rounds ?? NO_ROUNDS;

  // Tell the map which states train the model, and un-tell it on the way out.
  useEffect(() => {
    if (!onSilos) return;
    const states = rounds[rounds.length - 1]?.per_silo.map((s) => s.state) ?? [];
    onSilos(states);
    return () => onSilos([]);
  }, [onSilos, rounds]);

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  const replay = useCallback(() => {
    if (rounds.length < 2) return;
    if (timer.current) window.clearInterval(timer.current);
    // Somebody who has asked for less motion still wants the answer, so they
    // get the finished curve rather than a slower version of the animation.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setReplayAt(rounds.length - 1);
      return;
    }
    setReplayAt(0);
    timer.current = window.setInterval(() => {
      setReplayAt((at) => {
        const next = (at ?? 0) + 1;
        if (next >= rounds.length - 1) {
          if (timer.current) window.clearInterval(timer.current);
          timer.current = null;
          return rounds.length - 1;
        }
        return next;
      });
    }, REPLAY_STEP_MS);
  }, [rounds.length]);

  const replaying = replayAt != null && replayAt < rounds.length - 1;
  // Everything below reads this round, so the table, the counter and the curve
  // can never disagree about where the replay has got to.
  const shown = replayAt == null ? rounds[rounds.length - 1] : rounds[replayAt];
  const last = shown;
  const shapes = Object.entries(data?.tensor_shapes ?? {});
  const rowsSoFar =
    replayAt == null
      ? data?.raw_rows_transmitted ?? 0
      : rounds.slice(0, replayAt + 1).reduce((n, r) => n + r.raw_rows_transmitted, 0);
  const weighting = shown ? mostDownWeighted(shown.per_silo) : null;
  // Counted from the recorded tensor shapes, so bytes ÷ numbers is a check on
  // the measurement rather than an assumption about it.
  const params = shapes.reduce((n, [, shape]) => n + shape.reduce((a, b) => a * b, 1), 0);
  const bytesPerNumber =
    params > 0 && typeof data?.bytes_per_round === "number" ? data.bytes_per_round / params : null;
  const oneDay =
    rounds.length > 0 && rounds.every((r) => day(r.completed_at) === day(rounds[0].completed_at));
  const err = (v: number | null | undefined) => (voice === "official" ? shareOfUse(v) : mae(v));
  const silos = data?.silos ?? 0;
  const beats = data?.final_improvement_pct != null && data.final_improvement_pct > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 py-2.5">
        <div className="flex items-start justify-between gap-2">
          <h2 className="text-[13px] font-semibold text-ink">Federated training · संघीय प्रशिक्षण</h2>
          <div role="group" aria-label="Wording" className="flex shrink-0 gap-1">
            {(["official", "technical"] as Voice[]).map((v) => (
              <button
                key={v}
                onClick={() => setVoice(v)}
                aria-pressed={voice === v}
                className={`min-h-7 rounded px-2 text-[11px] font-medium ${
                  voice === v ? "bg-brand text-white" : "border border-line text-ink-2"
                }`}
              >
                {v === "official" ? "Plain" : "Technical"}
              </button>
            ))}
          </div>
        </div>
        {data?.available ? (
          <>
            <p className="mt-1 text-[12.5px] leading-snug text-ink">
              <span className="font-semibold">{data.total_windows.toLocaleString("en-IN")}</span> training
              examples stayed in their states. Each state sent{" "}
              <span className="font-semibold">{bytes(data.bytes_per_round)}</span> of model weights per round.
            </p>
            <Topology states={(last?.per_silo ?? []).map((s) => named(s.state))} perRound={bytes(data.bytes_per_round)} />
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              <button
                onClick={replay}
                disabled={replaying || rounds.length < 2}
                className="min-h-8 rounded-md border border-brand bg-brand/[0.04] px-3 py-1 text-left text-[12.5px] font-medium text-brand hover:bg-brand/10 focus:ring-2 focus:ring-brand/30 focus:outline-none disabled:opacity-55"
              >
                {replaying
                  ? `Round ${shown?.round_no ?? 0} of ${rounds[rounds.length - 1]?.round_no ?? 0}`
                  : `Replay a recorded run (${rounds.length ? day(rounds[0].completed_at) : "—"}, ${silos} states on Flower)`}
              </button>
              {replayAt != null && !replaying && (
                <button
                  onClick={() => setReplayAt(null)}
                  className="text-[12px] font-medium text-ink-3 hover:text-ink"
                >
                  Show the finished run
                </button>
              )}
            </div>
            <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
              A replay, not a new training: the rounds below were written by the aggregator when the
              run happened, and pressing this re-reads them in order.
            </p>

            {canTrain && live && (live.available || training) && (
              <div className="mt-2 border-t border-line pt-2">
                <button
                  onClick={runNextRound}
                  disabled={training}
                  className="h-8 rounded-md bg-brand px-3 text-[12.5px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:outline-none disabled:opacity-60"
                >
                  {training ? `Training round ${job?.round_no}…` : "Run next round"}
                </button>
                <p className="mt-1 text-[11px] leading-snug text-ink-3">
                  A real round: the state silos train on their own rows and send back weights, which
                  are checked, averaged and scored. It takes about two minutes.
                </p>
                {liveError && <p className="mt-1 text-[11.5px] text-crit">{liveError}</p>}
                {job && (training || job.status !== "running") && (
                  <ol className="mt-1.5 space-y-0.5" aria-live="polite">
                    {job.phases.map((p) => (
                      <li key={p.label} className="flex items-baseline justify-between gap-2 text-[11.5px]">
                        <span className="text-ink-2">
                          <span className="text-ok" aria-hidden="true">✓</span> {p.label}
                        </span>
                        <span className="shrink-0 font-mono text-[10.5px] text-ink-3">+{p.at_s.toFixed(1)}s</span>
                      </li>
                    ))}
                    {training && <li className="text-[11.5px] text-ink-3">Working…</li>}
                    {job.status === "done" && (
                      <li className="text-[11.5px] font-medium text-ok">
                        Round {job.round_no} recorded — see the highlighted row below.
                      </li>
                    )}
                    {job.status === "failed" && (
                      <li className="text-[11.5px] text-crit">The round stopped: {job.error}</li>
                    )}
                  </ol>
                )}
              </div>
            )}
          </>
        ) : (
          <p className="mt-0.5 text-[12px] text-ink-2">
            States train one forecasting model. Each keeps its own facility rows; only model weights move.
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="p-3 text-[12.5px] text-crit">{error}</p>
        ) : loading && !data ? (
          <p className="p-3 text-[12.5px] text-ink-3">Loading the last run…</p>
        ) : !data?.available ? (
          <div className="p-3">
            <p className="text-[12.5px] text-ink-2">{data?.note}</p>
          </div>
        ) : (
          <>
            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                Accuracy, against the rule it replaces
              </div>
              {replayAt == null && data.final_mae != null && data.baseline_mae != null && (
                <p className="mt-1 text-[12.5px] leading-snug text-ink">
                  <span className="font-mono font-semibold">{mae(data.final_mae)}</span> vs{" "}
                  <span className="font-mono font-semibold">{mae(data.baseline_mae)}</span> —{" "}
                  {beats
                    ? `${data.final_improvement_pct}% lower error than the burn-rate rule, on held-out weeks.`
                    : "the shared model does not beat the burn-rate rule in this run."}
                </p>
              )}
              <Curve
                rounds={data.rounds}
                baseline={data.baseline_mae}
                upTo={replayAt}
                beatsFrom={data.beats_baseline_from_round}
              />
              <p className="mt-1 text-[10.5px] leading-snug text-ink-3">
                Untrained start (round 0): {mae(data.untrained_mae)} — about ten times worse, so it is
                named here rather than drawn, and the chart shows the rounds that were trained.
              </p>
              <Row
                label={replayAt == null ? `${L.error}, final model` : `${L.error} at round ${shown?.round_no}`}
                value={err(shown?.global_val_mae)}
                strong
              />
              <Row label={L.baseline} value={err(data.baseline_mae)} />
              <Row label={L.untrained} value={err(data.untrained_mae)} />
              <Row label={L.best} value={err(data.best_mae)} />
              <p className="mt-1 text-[10.5px] leading-snug text-ink-3">
                The model predicts next week's daily use as a multiple of the last four weeks', so
                an error of {mae(data.final_mae)} means it is off by about{" "}
                {data.final_mae != null ? (data.final_mae * 100).toFixed(0) : "—"}% of a centre's usual daily
                use. Scored on weeks the model never trained on. Synthetic data.
              </p>
            </div>

            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                What left each state
              </div>
              <div className="mt-1.5 rounded-md border border-ok/30 bg-ok/5 px-2 py-1.5">
                <div className="flex items-baseline justify-between">
                  <span className="text-[11.5px] font-medium text-ink">{L.records}</span>
                  <span className="font-mono text-[15px] font-semibold tabular-nums text-ok">
                    {rowsSoFar}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] leading-snug text-ink-2">
                  0 records per round. What each state sent instead:{" "}
                  <span className="font-mono">{exactBytes(data.bytes_per_round)}</span> of model
                  weights
                  {params > 0 && bytesPerNumber != null && (
                    <>
                      {" "}
                      — {params.toLocaleString("en-IN")} numbers × {Number.isInteger(bytesPerNumber) ? bytesPerNumber : bytesPerNumber.toFixed(2)} bytes
                    </>
                  )}
                  .
                </p>
              </div>
              <div className="mt-1.5">
                <Row
                  label={L.weights}
                  value={`${exactBytes(data.bytes_per_round)} (${bytes(data.bytes_per_round)})`}
                  strong
                />
                <Row
                  label={L.total}
                  value={`${exactBytes(data.upload_bytes_total)} (${bytes(data.upload_bytes_total)})`}
                />
                <p className="text-[10.5px] leading-snug text-ink-3">
                  {data.training_rounds} training rounds × {silos} states ×{" "}
                  {exactBytes(data.bytes_per_round)}. Round 0 is the untrained model scored by the
                  aggregator; no state uploads anything for it.
                </p>
                <Row label="States reporting" value={String(last?.silos_reporting ?? "—")} />
              </div>
              <details className="mt-1.5 rounded-md border border-line bg-canvas px-2 py-1.5">
                <summary className="cursor-pointer text-[11.5px] font-medium text-ink-2">Audit details</summary>
                <p className="mt-1 font-mono text-[10.5px] text-ink-3">
                  {data.strategy} · run {data.run_id?.slice(0, 12)} · {data.rounds.length} recorded rounds
                </p>
                <p className="mt-1 text-[11px] leading-snug text-ink-2">
                  FedProx: keeps each state's training close to the shared model, because states'
                  seasons differ.
                </p>
                <p className="mt-1 text-[11px] leading-snug text-ink-2">
                  The record count is asserted by the aggregator before each round is recorded — a
                  reply carrying anything but weights and scalar numbers stops the round.
                </p>
                <div className="mt-1.5 text-[11px] font-medium text-ink-2">{L.hash}</div>
                <code className="mt-0.5 block break-all font-mono text-[10.5px] text-ink-3">
                  {last?.weights_sha256 ?? "—"}
                </code>
                <p className="mt-0.5 text-[10.5px] leading-snug text-ink-3">
                  The size is the same every round because the model's shape never changes; the
                  hash changes every round because the numbers inside it do.
                </p>
                {shapes.length > 0 && (
                  <>
                    <div className="mt-1.5 text-[11px] font-medium text-ink-2">
                      Layers in the model ({shapes.length} tensors)
                    </div>
                    <div className="mt-0.5 space-y-0.5">
                      {shapes.map(([name, shape]) => (
                        <div key={name} className="flex justify-between gap-2 font-mono text-[10.5px] text-ink-3">
                          <span className="truncate">{name}</span>
                          <span className="shrink-0">[{shape.join(" × ")}]</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </details>
            </div>

            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                Per state, {replayAt == null ? "last round" : `round ${shown?.round_no}`}
              </div>
              <p className="mt-1 text-[11px] leading-snug text-ink-3">
                A state's examples are scaled by its receipt discipline and nothing else: a score
                that falls as more of its warehouse consignments in the last 60 days go unconfirmed
                past their window (counted in full) or arrive short (counted half). A state that
                confirms fewer of its deliveries carries less of the shared model.
              </p>
              {replayAt == null && data.silos_changed_since_run.length > 0 && (
                <p role="note" className="mt-1.5 rounded border border-line bg-canvas px-2 py-1.5 text-[11px] leading-snug text-ink">
                  <span className="font-semibold">This run describes an earlier dataset.</span> It was
                  recorded on {rounds.length ? day(rounds[0].completed_at) : "an earlier day"}, and
                  today's ledger gives {data.silos_changed_since_run.map(named).join(" and ")} a
                  materially different score: the data behind them has changed since the run, as it
                  does when the demonstration data is reloaded. The "recorded" columns are the run's
                  own figures, as stored then, and the model in use is that run's model.
                </p>
              )}
              {weighting && (
                <p className="mt-1 text-[11px] leading-snug text-ink-2">
                  In the recorded run, {named(weighting.worst.state)} is the clearest case:{" "}
                  {weighting.worst.flagged_pct.toFixed(1)}% of its warehouse consignments in the 60
                  days before the run went unconfirmed or arrived short, against{" "}
                  {weighting.best.flagged_pct.toFixed(1)}% in {named(weighting.best.state)} — so its{" "}
                  {weighting.worst.windows.toLocaleString("en-IN")} examples count as{" "}
                  {weighting.worst.counts_as.toLocaleString("en-IN")}, {weighting.lostPct}% less
                  weight in the shared model.{" "}
                  <span className="text-ink-3">
                    Synthetic data: the weak state was chosen at random by the seed, so this says
                    nothing about {named(weighting.worst.state)}.
                  </span>
                  {onOpenLedger && (
                    <>
                      {" "}
                      <button
                        type="button"
                        onClick={() => onOpenLedger(weighting.worst.state)}
                        className="font-medium text-brand underline-offset-2 hover:underline"
                      >
                        Open {named(weighting.worst.state)}'s movement ledger →
                      </button>
                    </>
                  )}
                </p>
              )}
              <table className="mt-1.5 w-full text-[11.5px]">
                <thead>
                  <tr className="text-ink-3">
                    <th className="py-1 text-left font-medium">State</th>
                    <th className="py-1 text-right font-medium">{L.windows}</th>
                    <th className="py-1 text-right font-medium">{L.trust}, recorded</th>
                    <th className="py-1 text-right font-medium">{L.countsAs}</th>
                    <th className="py-1 text-right font-medium">Today's ledger</th>
                  </tr>
                </thead>
                <tbody>
                  {(last?.per_silo ?? []).map((s) => (
                    <tr key={s.state} className="border-t border-line">
                      <td className="py-1 font-medium text-ink">{named(s.state)}</td>
                      <td className="py-1 text-right font-mono tabular-nums text-ink-2">
                        {s.windows.toLocaleString("en-IN")}
                      </td>
                      <td className="py-1 text-right font-mono tabular-nums text-ink-2">
                        {s.trust.toFixed(3)}
                        {s.trust < 0.5 && <span className="ml-1 font-sans text-[10px] text-crit">low</span>}
                      </td>
                      <td className="py-1 text-right font-mono tabular-nums text-ink">
                        {s.counts_as.toLocaleString("en-IN")}
                      </td>
                      <td
                        className="py-1 text-right font-mono tabular-nums text-ink-2"
                        title={
                          s.flagged_pct_now != null
                            ? `${s.flagged_pct_now.toFixed(1)}% of its consignments in the last 60 days unconfirmed or short, from the ledger now`
                            : undefined
                        }
                      >
                        {replayAt != null || s.trust_now == null ? "—" : s.trust_now.toFixed(3)}
                        {replayAt == null && data.silos_changed_since_run.includes(s.state) && (
                          <span className="ml-1 font-sans text-[10px] text-ink">changed</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="border-b border-line px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                Round by round
              </div>
              <p className="mt-1 text-[11px] text-ink-3">
                {oneDay
                  ? `Each row written by the aggregator as its round finished, ${day(rounds[0].completed_at)}, your local time.`
                  : "Each row written by the aggregator as its round finished, your local time."}
              </p>
              <table className="mt-1.5 w-full font-mono text-[11px] tabular-nums">
                <thead>
                  <tr className="text-left font-sans text-ink-3">
                    <th className="py-1 font-medium">Round</th>
                    <th className="py-1 font-medium">Finished</th>
                    <th className="py-1 text-right font-medium">{L.mae}</th>
                    <th className="py-1 text-right font-medium">Weights</th>
                    <th className="py-1 pl-2 font-medium">Hash</th>
                    <th className="py-1 text-right font-medium">Records sent</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rounds.map((r, i) => (
                    <tr
                      key={r.round_no}
                      data-round={r.round_no}
                      className={`border-t border-line text-ink-2 ${
                        replayAt != null && i > replayAt ? "opacity-35" : ""
                      } ${r.round_no === freshRound ? "bg-ok/10 font-semibold text-ink" : ""}`}
                    >
                      <td className="py-1">
                        {r.round_no}
                        {r.round_no === 0 && <span className="ml-1 font-sans text-[10px] text-ink-3">untrained</span>}
                      </td>
                      <td className="py-1" title={new Date(r.completed_at).toString()}>
                        {oneDay ? clock(r.completed_at) : `${day(r.completed_at)} ${clock(r.completed_at)}`}
                      </td>
                      <td className="py-1 text-right">{mae(r.global_val_mae)}</td>
                      <td className="py-1 text-right">{exactBytes(r.bytes_transmitted)}</td>
                      <td className="py-1 pl-2 text-ink-3" title={r.weights_sha256 ?? undefined}>
                        {r.weights_sha256?.slice(0, 8) ?? "—"}
                      </td>
                      {/* The zero is the finding this subsystem exists to
                          produce, so it is set in the success colour rather
                          than the grey of a column nobody filled in. */}
                      <td
                        className={`py-1 text-right font-semibold ${
                          r.raw_rows_transmitted === 0 ? "text-ok" : "text-crit"
                        }`}
                        title={
                          r.raw_rows_transmitted === 0
                            ? "No facility record left its state this round"
                            : "Facility records left the state this round"
                        }
                      >
                        {r.raw_rows_transmitted.toLocaleString("en-IN")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="px-3 py-2.5">
              <div className="text-[10.5px] font-semibold tracking-[0.09em] text-ink-3 uppercase">
                What this does not show
              </div>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-snug text-ink-2">
                <li>
                  No differential privacy and no secure aggregation: the aggregator sees each state's
                  weights. Both are future work.
                </li>
                <li>
                  This prototype uses one synthetic database standing in for four state stores. The
                  federated boundary is the query each state's process runs, which reads only its
                  own state's rows.
                </li>
                <li>
                  No model trained by one state alone has been compared with the shared one, so
                  nothing here says the shared model is better than a state's own.
                </li>
              </ul>
              {canTrain && live && !(live.available || training) && (
                <p className="mt-2 text-[10.5px] leading-snug text-ink-3">
                  Run next round is off here. {live.reason}
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

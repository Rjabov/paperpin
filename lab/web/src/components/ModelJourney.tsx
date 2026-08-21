import { useEffect, useRef, useState } from "react";
import type { Timings } from "../types";

const fmt = (s: number) => (s >= 1 ? `${s.toFixed(2)}s` : `${Math.round(s * 1000)}ms`);

/** The model's line on the receipt: who ran, what it cost, how long it took.
 * While running it names the step in flight; on error it names the failure.
 * paperpin's own stages live in the pipeline strip, not here. */
export function ModelJourney({ model, status, events, tokenUsage, costUsd, costApprox,
                               latencyMs, timings, color = "var(--text)", error, right }: {
  model: string;
  status: "queued" | "running" | "done" | "error";
  events?: { stage: string; start_s: number; end_s?: number }[] | null;
  tokenUsage?: { prompt_tokens: number; output_tokens: number } | null;
  costUsd?: number | null;
  costApprox?: boolean;
  latencyMs?: number | null;
  timings?: Timings | null;
  color?: string;
  error?: string | null;
  right?: React.ReactNode;
}) {
  const running = status === "queued" || status === "running";
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((v) => v + 1), 150);
    return () => clearInterval(t);
  }, [running]);

  const isByo = model === "byo";
  const modelEv = (events ?? []).find((e) => e.stage.startsWith("model"));

  // elapsed for the open step ticks with local time, anchored on the last known event
  const lastKnown = events?.length ? Math.max(...events.map((e) => e.end_s ?? e.start_s)) : 0;
  const wallRef = useRef({ k: -1, t: 0 });
  if (wallRef.current.k !== lastKnown) wallRef.current = { k: lastKnown, t: performance.now() };
  const liveExtra = running ? (performance.now() - wallRef.current.t) / 1000 : 0;

  const totalS = latencyMs != null ? latencyMs / 1000
    : timings ? (timings.extract_s ?? 0) + (timings.ground_s ?? 0) + (timings.native_s ?? 0)
    : null;
  const tokens = tokenUsage
    ? `${(tokenUsage.prompt_tokens + tokenUsage.output_tokens).toLocaleString()} tok`
    : isByo ? "0 tok" : null;
  const cost = costUsd != null ? `${costApprox ? "~" : ""}$${costUsd.toFixed(4)}`
    : isByo ? "$0" : null;

  let line: string;
  let lineColor = "var(--muted)";
  if (status === "error") {
    line = `failed at ${modelEv?.end_s != null ? "results" : "api call"}`;
    lineColor = "var(--bad)";
  } else if (running) {
    if (isByo) {
      line = "pinning…";
    } else if (modelEv && modelEv.end_s == null) {
      line = `api call ${fmt(Math.max(0, lastKnown - modelEv.start_s + liveExtra))}…`;
    } else if (modelEv) {
      line = "pinning…";
    } else {
      line = "doc sent…";
    }
    lineColor = "var(--warn)";
  } else {
    line = [tokens, cost, totalS != null ? fmt(totalS) : null].filter(Boolean).join(" · ");
  }

  return (
    <div className="flex min-w-0 items-baseline gap-2" title={error ?? undefined}>
      <b className="flex-none truncate text-[11.5px]"
        style={{ fontFamily: "var(--display)", color }}>
        {isByo ? "paperpin only" : model.replace("gemini/", "")}
      </b>
      <span className="min-w-0 flex-1 truncate text-[11px]"
        style={{ fontFamily: "var(--mono)", color: lineColor }}>
        {line}
      </span>
      {right}
    </div>
  );
}

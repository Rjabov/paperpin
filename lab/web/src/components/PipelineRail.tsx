import { useEffect, useRef, useState } from "react";

export interface StageEvent {
  stage: string;
  start_s: number;
  end_s?: number;
  info?: { segments?: number; cache_hit?: boolean; pages?: number;
           rows?: number; fields?: number; model?: string };
}

export interface PipelineProfile {
  total_s?: number; cpu_s?: number; peak_ram_mb?: number | null;
  model_read_s?: number;
}

const fmt = (s: number) => (s >= 1 ? `${s.toFixed(2)}s` : `${Math.round(s * 1000)}ms`);

function subInfo(ev: StageEvent): string {
  const i = ev.info ?? {};
  const bits: string[] = [];
  if (i.pages != null) bits.push(`${i.pages} page${i.pages > 1 ? "s" : ""}`);
  if (i.segments != null) bits.push(`${i.segments} segments`);
  if (i.cache_hit) bits.push("cache hit");
  if (i.rows != null) bits.push(`${i.rows} rows`);
  if (i.fields != null) bits.push(`${i.fields} fields`);
  return bits.join(" · ");
}

/** Horizontal pipeline strip — inked tabs under the header. Live mode ticks
 * the open stage; done stages keep their true durations, and the profile
 * total closes the line when the run finishes. */
export function PipelineStrip({ events, profile, running }: {
  events: StageEvent[] | null;
  profile?: PipelineProfile | null;
  running: boolean;
}) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((v) => v + 1), 120);
    return () => clearInterval(t);
  }, [running]);

  // during a live run, elapsed for the open stage ticks with local time
  const lastKnown = events?.length
    ? Math.max(...events.map((e) => e.end_s ?? e.start_s)) : 0;
  const wallRef = useRef({ k: -1, t: 0 });
  if (wallRef.current.k !== lastKnown) {
    wallRef.current = { k: lastKnown, t: performance.now() };
  }
  const liveExtra = running ? (performance.now() - wallRef.current.t) / 1000 : 0;

  if (!events?.length && !running) return null;

  return (
    <div className="flex items-center overflow-x-auto border-b px-5"
      style={{ borderColor: "var(--line)", background: "var(--bg2)" }}>
      <span className="label flex-none pr-2 py-2.5">pipeline</span>
      {(events ?? []).map((ev, i) => {
        const done = ev.end_s != null;
        const dur = done ? ev.end_s! - ev.start_s
          : Math.max(0, lastKnown - ev.start_s + liveExtra);
        const isCurrent = !done && running;
        return (
          <div key={`${ev.stage}|${i}`} title={subInfo(ev)}
            className="flex flex-none items-baseline gap-1.5 px-3 py-2.5"
            style={{ boxShadow: done ? "inset 0 -2px var(--accent)"
                                     : isCurrent ? "inset 0 -2px var(--warn)" : "none" }}>
            <span className="label" style={{
              color: done ? "var(--text)" : isCurrent ? "var(--text)" : "var(--muted)" }}>
              {ev.stage}
            </span>
            <span className="text-[11px]" style={{ fontFamily: "var(--mono)",
              color: isCurrent ? "var(--warn)" : "var(--muted)" }}>
              {isCurrent ? `${fmt(dur)}…` : fmt(dur)}
            </span>
          </div>
        );
      })}
      {running && !events?.length && (
        <span className="label py-2.5" style={{ color: "var(--warn)" }}>starting…</span>
      )}
      {!running && profile && (
        <span className="ml-auto flex-none py-2.5 pl-4 text-[11px]"
          style={{ fontFamily: "var(--mono)", color: "var(--muted)" }}>
          total <b style={{ color: "var(--text)" }}>{fmt(profile.total_s ?? 0)}</b>
          {" "}· cpu {fmt(profile.cpu_s ?? 0)}
          {profile.peak_ram_mb ? ` · peak ${Math.round(profile.peak_ram_mb)}MB` : ""}
        </span>
      )}
    </div>
  );
}

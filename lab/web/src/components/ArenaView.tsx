import { useMemo, useState } from "react";
import { api } from "../api";
import type { Arena, DocumentMeta, Run, Status } from "../types";
import { ModelJourney } from "./ModelJourney";
import { PipelineStrip, type PipelineProfile, type StageEvent } from "./PipelineRail";
import { STATUS_COLOR, StatusDot } from "./StatusBits";
import { ZoomBar, useZoom } from "./Zoom";

const MODEL_COLORS = ["#2dd4a7", "#53b4ff", "#f5b83d", "#e879f9"];

function Chip({ on, color, dashed, label, title, onClick }: {
  on: boolean; color: string; dashed?: boolean; label: string;
  title: string; onClick: () => void;
}) {
  return (
    <button onClick={onClick} title={title}
      className="rounded-md px-2 py-0.5 text-[11px] transition-all"
      style={on
        ? { background: `${color}22`, border: `1px ${dashed ? "dashed" : "solid"} ${color}`,
            color, boxShadow: `0 0 8px ${color}44` }
        : { background: "transparent", border: "1px solid var(--line)",
            color: "var(--muted)", opacity: 0.75 }}>
      {label}
    </button>
  );
}

export function ArenaView({ doc, arena }: { doc: DocumentMeta; arena: Arena }) {
  const [layers, setLayers] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    arena.models.forEach((m, i) => {
      init[`${m}|pin`] = i === 0;
      init[`${m}|native`] = i === 0;
    });
    return init;
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [timelineModel, setTimelineModel] = useState<string | null>(null);
  const [showPipeline, setShowPipeline] = useState(false);
  const { zoom, zoomIn, zoomOut, reset, onWheel } = useZoom();
  const running = arena.status === "queued" || arena.status === "running";

  const fieldNames = useMemo(() => {
    const names = new Set<string>();
    arena.entries.forEach((e) => {
      if (e.result) Object.keys(e.result.fields).forEach((n) => names.add(n));
    });
    return [...names].sort((a, b) => {
      const fa = arena.entries.find((e) => e.result?.fields[a]?.bbox)?.result?.fields[a];
      const fb = arena.entries.find((e) => e.result?.fields[b]?.bbox)?.result?.fields[b];
      return (fa?.bbox?.[1] ?? 9) - (fb?.bbox?.[1] ?? 9);
    });
  }, [arena]);

  const toggle = (key: string) => setLayers((l) => ({ ...l, [key]: !l[key] }));

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      {/* canvas with layered boxes */}
      <div className="paper relative flex min-w-0 flex-[1.1] flex-col items-center gap-4 overflow-auto p-5"
        onWheel={onWheel}>
        {/* layer picker: sticky so multipage scrolling keeps the controls */}
        <div className="sticky top-0 z-20 self-stretch rounded-lg border text-[11.5px]"
          style={{ borderColor: "var(--line)", background: "rgba(247,244,236,.94)",
                   backdropFilter: "blur(4px)" }}>
          <div className="flex items-center gap-2 border-b px-3 py-1.5"
            style={{ borderColor: "var(--line)", color: "var(--muted)" }}>
            <span className="text-[10.5px] uppercase tracking-widest">boxes on the document</span>
            <span className="ml-auto flex items-center gap-2">
              <ZoomBar zoom={zoom} zoomIn={zoomIn} zoomOut={zoomOut} reset={reset} sticky={false} />
              <button onClick={() => setShowHelp((v) => !v)}
                className="flex h-[17px] w-[17px] items-center justify-center rounded-full border text-[10px]"
                title="what am I looking at?"
                style={{ borderColor: showHelp ? "var(--accent)" : "var(--line)",
                         color: showHelp ? "var(--accent)" : "var(--muted)" }}>
                i
              </button>
            </span>
          </div>
          {showHelp && (
            <div className="border-b px-3 py-2 leading-relaxed"
              style={{ borderColor: "var(--line)", color: "var(--muted)" }}>
              <div><b style={{ color: "var(--text)" }}>── paperpin pins (solid)</b> — where the value REALLY is:
                found deterministically in the document's own text geometry. Cannot be hallucinated.</div>
              <div><b style={{ color: "var(--text)" }}>╌╌ model's own boxes (dashed)</b> — coordinates the model
                CLAIMED when we asked it. Drawn exactly as returned, never corrected.</div>
              <div>Each model extracts its own values, so each model gets its own pins —
                one color per model. The gap between a solid and a dashed box of the same
                color = that model's grounding error.</div>
            </div>
          )}
          <div className="flex flex-col gap-1 px-2 py-1.5">
            {arena.models.map((m, i) => {
              const color = MODEL_COLORS[i % 4];
              return (
                <div key={m} className="flex items-center gap-2 rounded-md px-1.5 py-1"
                  style={{ borderLeft: `3px solid ${color}` }}>
                  <b className="min-w-0 flex-1 truncate" style={{ color }}>
                    {m.replace("gemini/", "")}
                  </b>
                  <Chip on={!!layers[`${m}|pin`]} color={color}
                    label="── paperpin pins"
                    title="boxes computed from the document text itself — the audit-grade ones"
                    onClick={() => toggle(`${m}|pin`)} />
                  <Chip on={!!layers[`${m}|native`]} color={color} dashed
                    label="╌╌ its own boxes"
                    title="coordinates this model claimed for its values — shown verbatim for comparison"
                    onClick={() => toggle(`${m}|native`)} />
                </div>
              );
            })}
          </div>
        </div>

        {doc.pages.map((pg) => (
          <div key={pg.index} className="relative flex-none overflow-hidden rounded-[3px] bg-white"
            style={{ boxShadow: "0 0 0 1px var(--line)",
                     width: `${zoom * 100}%`, maxWidth: zoom <= 1 ? "100%" : undefined }}>
            <img src={api.pageUrl(doc.id, pg.index, zoom > 1.6 ? 2400 : 1400)} alt={`page ${pg.index + 1}`}
              className="block h-auto w-full"
              style={{ filter: "brightness(.94) contrast(1.02)" }} draggable={false} />
            {!running && arena.entries.map((e, mi) => {
              const color = MODEL_COLORS[mi % 4];
              const pinOn = layers[`${e.model}|pin`];
              const natOn = layers[`${e.model}|native`];
              return (
                <span key={e.model}>
                  {pinOn && e.result && Object.values(e.result.fields)
                    .filter((f) => f.page === pg.index && f.bbox)
                    .map((f) => (
                      <div key={`p-${f.name}`}
                        className="absolute cursor-pointer rounded-[2px]"
                        onClick={() => setSelected(f.name)}
                        style={{
                          left: `${f.bbox![0] * 100}%`, top: `${f.bbox![1] * 100}%`,
                          width: `${(f.bbox![2] - f.bbox![0]) * 100}%`,
                          height: `${(f.bbox![3] - f.bbox![1]) * 100}%`,
                          border: `2px solid ${color}`,
                          background: `${color}26`,
                          boxShadow: selected === f.name
                            ? `0 0 0 3px ${color}aa, 0 0 24px 6px ${color}66`
                            : `0 0 8px 1px ${color}cc, 0 0 22px 4px ${color}44`,
                          zIndex: selected === f.name ? 5 : 3,
                        }} />
                    ))}
                  {natOn && Object.entries(e.native)
                    .filter(([n, b]) => n !== "_error" && !!b?.xyxy &&
                      (b.page ?? 0) === pg.index)
                    .map(([n, b]) => (
                      <div key={`n-${n}`}
                        className="absolute cursor-pointer rounded-[2px]"
                        onClick={() => setSelected(n)}
                        style={{
                          left: `${b.xyxy![0] * 100}%`, top: `${b.xyxy![1] * 100}%`,
                          width: `${(b.xyxy![2] - b.xyxy![0]) * 100}%`,
                          height: `${(b.xyxy![3] - b.xyxy![1]) * 100}%`,
                          border: `2px dashed ${color}`,
                          boxShadow: `0 0 10px 1px ${color}77`,
                          opacity: 0.9,
                          zIndex: selected === n ? 5 : 2,
                        }} />
                    ))}
                </span>
              );
            })}
          </div>
        ))}
      </div>

      {/* scoreboard */}
      <div className="flex w-[460px] min-w-[380px] flex-col border-l"
        style={{ borderColor: "var(--line)", background: "var(--panel2)" }}>
        <div className="flex items-center gap-2 border-b px-4 py-2.5" style={{ borderColor: "var(--line)" }}>
          <span className="text-[13px] font-semibold">⚔ arena #{arena.id}</span>
          <span className="text-[11.5px]" style={{ color: "var(--muted)" }}>
            {running ? arena.status : "done"}
          </span>
          <a className="ml-auto rounded-md border px-2.5 py-1 text-[11px]"
            style={{ borderColor: "var(--line)", color: "var(--muted)" }}
            href={api.arenaExportUrl(arena.id)} download>
            ⤓ download json
          </a>
        </div>

        {/* summary cards */}
        <div className="grid gap-2 border-b p-3"
          style={{ borderColor: "var(--line)",
                   gridTemplateColumns: `repeat(${Math.min(2, arena.models.length)}, 1fr)` }}>
          {arena.entries.map((e, i) => (
            <div key={e.model} className="rounded-lg border p-2.5 text-[11.5px]"
              style={{ borderColor: `${MODEL_COLORS[i % 4]}44`, background: "var(--panel)" }}>
              <div className="mb-1 font-semibold" style={{ color: MODEL_COLORS[i % 4] }}>
                {e.model.replace("gemini/", "")}
              </div>
              {e.score ? (
                <div className="grid grid-cols-2 gap-x-2 gap-y-0.5" style={{ color: "var(--muted)" }}>
                  <span>located</span>
                  <b style={{ color: "var(--text)" }}>{e.score.located}/{e.score.n_fields}</b>
                  <span>hallucinations</span>
                  <b style={{ color: (e.score.statuses.not_found ?? 0) > 0 ? "var(--bad)" : "var(--text)" }}>
                    {e.score.statuses.not_found ?? 0} flagged
                  </b>
                  <span>native boxes</span>
                  <b style={{ color: "var(--text)" }}>{e.score.native_boxes}</b>
                  <span>IoU vs native</span>
                  <b style={{ color: "var(--text)" }}>
                    {e.score.mean_iou_vs_native ?? "—"}
                    {e.score.native_iou50_rate != null && ` (${Math.round(e.score.native_iou50_rate * 100)}%≥.5)`}
                    {e.score.native_agree_rate != null && ` · agree ${Math.round(e.score.native_agree_rate * 100)}%`}
                  </b>
                  <span>time</span>
                  <b style={{ color: "var(--text)" }}>
                    {e.score.timings
                      ? <>read {e.score.timings.extract_s?.toFixed(1)}s
                          · pin {e.score.timings.ground_s?.toFixed(1)}s
                          {e.score.timings.native_s != null && <> · boxes {e.score.timings.native_s.toFixed(1)}s</>}</>
                      : `${((e.score.latency_ms ?? 0) / 1000).toFixed(1)}s`}
                  </b>
                  <span>tokens</span>
                  <b style={{ color: "var(--text)" }}>
                    {e.score.token_usage
                      ? (e.score.token_usage.prompt_tokens + e.score.token_usage.output_tokens).toLocaleString()
                      : "—"}
                  </b>
                  <span>est. cost</span>
                  <b style={{ color: "var(--text)" }}
                     title="edit lab/server/pricing.py to adjust rates">
                    {e.score.cost_usd != null
                      ? `${e.score.cost_approx ? "~" : ""}$${e.score.cost_usd.toFixed(4)}`
                      : "— (unknown rate)"}
                  </b>
                </div>
              ) : (
                <div style={{ color: "var(--muted)" }}>{e.status}{e.error ? ` — ${e.error}` : "…"}</div>
              )}
            </div>
          ))}
        </div>

        {/* Timeline A — one model journey per model, always visible */}
        <div className="border-b px-3 py-2" style={{ borderColor: "var(--line)" }}>
          <div className="flex flex-col gap-1">
            {arena.models.map((m, i) => {
              const e = arena.entries.find((x) => x.model === m);
              return (
                <ModelJourney key={m}
                  model={m}
                  status={(e?.status ?? (running ? "queued" : "done")) as Run["status"]}
                  events={e?.progress}
                  tokenUsage={e?.score?.token_usage ?? null}
                  costUsd={e?.score?.cost_usd}
                  costApprox={e?.score?.cost_approx}
                  latencyMs={e?.score?.latency_ms}
                  timings={e?.score?.timings}
                  color={MODEL_COLORS[i % 4]}
                  error={e?.error}
                />
              );
            })}
          </div>
          <button onClick={() => setShowPipeline((v) => !v)}
            className="mt-1.5 rounded-md border px-2 py-0.5 text-[10.5px]"
            title="open the paperpin pipeline stage detail"
            style={showPipeline
              ? { borderColor: "var(--accent)", color: "var(--accent)" }
              : { borderColor: "var(--line)", color: "var(--muted)" }}>
            ⚙ pipeline {showPipeline ? "▾" : "▸"}
          </button>
        </div>

        {/* Timeline B — paperpin pipeline detail. Opens ONLY on click, never by itself. */}
        {showPipeline && (() => {
          const withEvents = arena.entries.filter((e) => e.progress?.length);
          if (!withEvents.length) {
            return (
              <div className="border-b px-4 py-2 text-[11.5px]"
                style={{ borderColor: "var(--line)", color: "var(--muted)" }}>
                no pipeline events yet…
              </div>
            );
          }
          const active = withEvents.find((e) => e.model === timelineModel) ?? withEvents[0];
          const profile = (active.result?.meta as { profile?: PipelineProfile } | undefined)?.profile;
          const entryRunning = active.status === "running" || active.status === "queued";
          return (
            <div className="border-b" style={{ borderColor: "var(--line)" }}>
              <div className="flex gap-1 px-3 pt-2">
                {withEvents.map((e) => (
                  <button key={e.model} onClick={() => setTimelineModel(e.model)}
                    className="rounded px-2 py-0.5 text-[10.5px]"
                    style={active.model === e.model
                      ? { color: MODEL_COLORS[arena.models.indexOf(e.model) % 4],
                          border: `1px solid ${MODEL_COLORS[arena.models.indexOf(e.model) % 4]}66` }
                      : { color: "var(--muted)", border: "1px solid transparent" }}>
                    {e.model.replace("gemini/", "")}
                    {(e.status === "running" || e.status === "queued") && " ●"}
                  </button>
                ))}
              </div>
              <PipelineStrip
                events={(active.progress ?? null) as StageEvent[] | null}
                profile={profile}
                running={entryRunning}
              />
            </div>
          );
        })()}

        {/* per-field comparison */}
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {fieldNames.map((name) => {
            const agree = arena.agreement[name];
            return (
              <div key={name}
                onClick={() => setSelected(name)}
                className="my-0.5 cursor-pointer rounded-lg border border-transparent px-2.5 py-1.5"
                style={selected === name ? { background: "var(--panel)", borderColor: "var(--accent)" }
                  : agree && !agree.all_agree ? { borderColor: "rgba(245,184,61,.35)" } : undefined}>
                <div className="flex items-center gap-2">
                  <span className="text-[12px]" style={{ color: "var(--muted)" }}>{name}</span>
                  {agree && !agree.all_agree && (
                    <span className="text-[10px]" style={{ color: "var(--warn)" }}>⚠ models disagree</span>
                  )}
                </div>
                {arena.entries.map((e, i) => {
                  const fr = e.result?.fields[name];
                  if (!fr) return null;
                  const nb = e.native[name];
                  return (
                    <div key={e.model} className="mt-0.5 flex items-start gap-2">
                      <span className="mt-1 h-2 w-2 flex-none rounded-full"
                        style={{ background: MODEL_COLORS[i % 4] }} />
                      <StatusDot status={fr.status as Status} />
                      <span className="min-w-0 flex-1 break-words text-[12px]"
                        style={{ fontFamily: "var(--mono)" }}>
                        {fr.value === null ? "∅" : String(fr.value)}
                      </span>
                      <span className="text-[10px]" style={{ color: STATUS_COLOR[fr.status as Status] }}>
                        {fr.status === "not_found" ? "NOT FOUND" : fr.status === "verified" ? "✓" : "~"}
                      </span>
                      {nb?.xyxy && fr.bbox && (
                        <span className="text-[10px]" style={{ color: "var(--muted)" }}>
                          IoU {(_iou(fr.bbox, nb.xyxy)).toFixed(2)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
          {running && (
            <div className="p-4 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
              running {arena.models.length} model{arena.models.length > 1 ? "s" : ""} —
              extraction + grounding + native-box pass each…
            </div>
          )}
        </div>
        <div className="border-t px-3 py-1.5 text-[10.5px]" style={{ borderColor: "var(--line)", color: "var(--muted)" }}>
          every run persists to ~/.paperpin/lab/lab.sqlite (results, raw responses, native boxes)
        </div>
      </div>
    </div>
  );
}

function _iou(a: number[], b: number[]): number {
  const ix0 = Math.max(a[0], b[0]), iy0 = Math.max(a[1], b[1]);
  const ix1 = Math.min(a[2], b[2]), iy1 = Math.min(a[3], b[3]);
  if (ix1 <= ix0 || iy1 <= iy0) return 0;
  const inter = (ix1 - ix0) * (iy1 - iy0);
  const ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
  return inter / Math.max(1e-12, ua);
}

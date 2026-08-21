import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ArenaView } from "./components/ArenaView";
import { DropZone } from "./components/DropZone";
import { RunComposer, type ComposerValue } from "./components/RunComposer";
import { ResultView } from "./components/ResultView";
import { PipelineStrip, type PipelineProfile } from "./components/PipelineRail";
import type { Arena, DocumentMeta, ModelOption, Run } from "./types";

// The public demo tells one story; the arena stays a local benchmark tool.
const ARENA_ENABLED = import.meta.env.VITE_ARENA === "1";

export default function App() {
  const [docs, setDocs] = useState<DocumentMeta[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [keySet, setKeySet] = useState(false);
  const [activeDoc, setActiveDoc] = useState<DocumentMeta | null>(null);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const [mode, setMode] = useState<"single" | "arena">("single");
  const [arenaModels, setArenaModels] = useState<string[]>([]);
  const [activeArena, setActiveArena] = useState<Arena | null>(null);
  const arenaTimer = useRef<number | null>(null);

  const [composer, setComposer] = useState<ComposerValue>({
    model: "byo", prompt: "", byoJson: "",
  });

  const loadMeta = useCallback(() => {
    api.settings().then((s) => {
      setKeySet(s.gemini_key_set);
      api.listModels().then((ms) => {
        setModels(ms);
        const flash = ms.find((m) => m.id === "gemini/gemini-flash-latest");
        if (s.gemini_key_set && flash) setComposer((c) => ({ ...c, model: flash.id }));
        const pro = ms.find((m) => m.id === "gemini/gemini-pro-latest");
        if (s.gemini_key_set) {
          setArenaModels([flash?.id, pro?.id].filter(Boolean) as string[]);
        }
      }).catch(() => {});
    }).catch(() => {});
  }, []);

  // Demo is session-scoped on purpose: only documents dropped in THIS tab
  // appear, and no stored run is ever auto-loaded. The server keeps its
  // local cache so repeat drops and re-pins stay fast; the UI stays clean.
  useEffect(() => {
    loadMeta();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      if (arenaTimer.current) window.clearTimeout(arenaTimer.current);
    };
  }, [loadMeta]);

  const saveKey = useCallback(
    (key: string) => api.saveSettings(key).then(loadMeta), [loadMeta]);

  const selectDoc = useCallback((d: DocumentMeta) => {
    setActiveDoc(d);
    setActiveRun(null);
    setActiveArena(null);
  }, []);

  const onFiles = useCallback(async (files: File[]) => {
    setError(null);
    setUploading(true);
    try {
      let last: DocumentMeta | null = null;
      const uploaded: DocumentMeta[] = [];
      for (const f of files) {
        last = await api.uploadDocument(f);
        uploaded.push(last);
      }
      setDocs((prev) => [
        ...uploaded.filter((u) => !prev.some((p) => p.id === u.id)),
        ...prev,
      ]);
      if (last) selectDoc(last);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setUploading(false);
    }
  }, [selectDoc]);

  const nativeWait = useRef(0);
  const poll = useCallback((runId: number) => {
    api.getRun(runId).then((r) => {
      setActiveRun(r);
      if (r.status === "queued" || r.status === "running") {
        nativeWait.current = 0;
        pollTimer.current = window.setTimeout(() => poll(runId), 350);
      } else if (r.status === "error") {
        setError(r.error ?? "run failed");
      } else if (r.model.startsWith("gemini")
                 && Object.keys(r.native ?? {}).length === 0
                 && nativeWait.current < 15) {
        // the model's own-boxes pass lands a few seconds after the result;
        // keep listening briefly so the dashed claims appear on their own
        nativeWait.current += 1;
        pollTimer.current = window.setTimeout(() => poll(runId), 1500);
      }
    }).catch((e) => setError(String(e)));
  }, []);

  const startRun = useCallback(() => {
    if (!activeDoc) return;
    setError(null);
    api.createRun({
      document_id: activeDoc.id,
      model: composer.model,
      schema_spec: null,
      prompt: composer.prompt || null,
      extraction: composer.model === "byo" ? JSON.parse(composer.byoJson) : null,
    }).then(({ run_id }) => {
      setActiveRun({ id: run_id, document_id: activeDoc.id, model: composer.model,
                     status: "queued", error: null, latency_ms: null,
                     token_usage: null, created_at: Date.now() / 1000 });
      poll(run_id);
    }).catch((e) => setError(String((e as Error).message ?? e)));
  }, [activeDoc, composer, poll]);

  const pollArena = useCallback((arenaId: number) => {
    api.getArena(arenaId).then((a) => {
      setActiveArena(a);
      if (a.status === "queued" || a.status === "running") {
        arenaTimer.current = window.setTimeout(() => pollArena(arenaId), 1200);
      } else {
        if (a.status === "error") setError(a.error ?? "arena failed");
      }
    }).catch((e) => setError(String(e)));
  }, []);

  const startArena = useCallback(() => {
    if (!activeDoc || arenaModels.length === 0) return;
    setError(null);
    api.createArena({
      document_id: activeDoc.id,
      models: arenaModels,
      schema_spec: null,
      prompt: composer.prompt || null,
    }).then(({ arena_id }) => {
      setActiveArena({ id: arena_id, document_id: activeDoc.id, status: "queued",
                       error: null, models: arenaModels, created_at: Date.now() / 1000,
                       entries: [], agreement: {} });
      pollArena(arena_id);
    }).catch((e) => setError(String((e as Error).message ?? e)));
  }, [activeDoc, arenaModels, composer, pollArena]);

  const repin = useCallback((fresh: boolean) => {
    if (!activeRun || activeRun.status !== "done") return;
    setError(null);
    api.repinRun(activeRun.id, fresh).then(({ run_id }) => {
      setActiveRun({ id: run_id, document_id: activeRun.document_id, model: "byo",
                     status: "queued", error: null, latency_ms: null,
                     token_usage: null, created_at: Date.now() / 1000 });
      poll(run_id);
    }).catch((e) => setError(String((e as Error).message ?? e)));
  }, [activeRun, poll]);

  const running = activeRun?.status === "queued" || activeRun?.status === "running";
  const arenaRunning = activeArena?.status === "queued" || activeArena?.status === "running";

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3.5 border-b px-5 py-3"
        style={{ borderColor: "var(--line)", background: "var(--bg2)" }}>
        <div className="text-[17px] font-extrabold lowercase"
          style={{ fontFamily: "var(--display)", letterSpacing: "-0.03em" }}>
          paperpin<span style={{ color: "var(--accent)" }}>.</span>
          <span className="ml-1.5 font-medium" style={{ color: "var(--muted)", letterSpacing: "0" }}>lab</span>
        </div>
        {activeDoc && (
          <div className="rounded-lg border px-3 py-1 text-[12.5px]"
            style={{ borderColor: "var(--line)", background: "var(--panel)", color: "var(--muted)" }}>
            ▤ <b style={{ color: "var(--text)" }}>{activeDoc.filename}</b> · {activeDoc.pages.length}p
            · {activeDoc.pages[0]?.route}
          </div>
        )}
        {activeRun?.status === "done" && (
          <div className="text-[11.5px]" style={{ color: "var(--muted)" }}>
            {activeRun.model === "byo" ? "paperpin only" : activeRun.model}
            {activeRun.timings
              ? <>
                  {(activeRun.timings.extract_s ?? 0) > 0.05 &&
                    <> · read {activeRun.timings.extract_s!.toFixed(1)}s</>}
                  {" "}· pin <b style={{ color: "var(--ok)" }}>
                    {activeRun.timings.ground_s?.toFixed(2)}s</b>
                </>
              : <> · {((activeRun.latency_ms ?? 0) / 1000).toFixed(1)}s</>}
            {activeRun.model === "byo"
              ? <> · no model · 0 tokens · $0</>
              : <>
                  {activeRun.token_usage &&
                    <> · {(activeRun.token_usage.prompt_tokens + activeRun.token_usage.output_tokens).toLocaleString()} tokens</>}
                  {activeRun.cost_usd != null &&
                    <> · {activeRun.cost_approx ? "~" : ""}${activeRun.cost_usd.toFixed(4)}</>}
                </>}
          </div>
        )}
        <div className="ml-auto text-[11.5px]" style={{ color: "var(--muted)" }}>
          {keySet ? <span><span style={{ color: "var(--ok)" }}>●</span> gemini key</span>
                  : <span><span style={{ color: "var(--np)" }}>●</span> offline (BYO only)</span>}
          <span className="ml-3">local only · zero telemetry</span>
        </div>
      </header>

      {error && (
        <div className="border-b px-5 py-2 text-[12.5px]"
          style={{ background: "#f2e3de", borderColor: "rgba(160,74,58,.45)", color: "var(--bad)" }}>
          ⚠ {error} <button className="ml-2 underline" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}

      {mode === "single" && activeRun && (running || activeRun.progress?.length) ? (
        <PipelineStrip
          events={activeRun.progress ?? null}
          profile={(activeRun.result?.meta as { profile?: PipelineProfile } | undefined)?.profile}
          running={!!running}
        />
      ) : null}

      <div className="flex min-h-0 flex-1">
        {/* left rail: documents + composer */}
        <div className="flex w-[300px] min-w-[260px] flex-col border-r"
          style={{ borderColor: "var(--line)", background: "var(--panel2)" }}>
          <DropZone onFiles={onFiles} busy={uploading} />
          <div className="min-h-0 flex-1 overflow-y-auto px-2">
            {docs.map((d) => (
              <div key={d.id}
                onClick={() => selectDoc(d)}
                className="my-1 cursor-pointer rounded-sm border px-3 py-2 text-[12.5px]"
                style={{
                  background: "var(--panel)",
                  borderColor: activeDoc?.id === d.id ? "var(--accent)" : "var(--line)",
                  borderLeft: activeDoc?.id === d.id
                    ? "3px solid var(--accent)" : "3px solid var(--line)",
                }}>
                <div className="truncate font-semibold" style={{ color: "var(--text)" }}>{d.filename}</div>
                <div className="text-[11px]" style={{ color: "var(--muted)", fontFamily: "var(--mono)" }}>
                  {d.pages.length} page{d.pages.length > 1 ? "s" : ""} · {d.pages[0]?.route}
                </div>
              </div>
            ))}
          </div>
          {activeDoc && ARENA_ENABLED && (
            <div className="flex gap-1 border-t px-3 pt-2.5" style={{ borderColor: "var(--line)" }}>
              {(["single", "arena"] as const).map((m) => (
                <button key={m} onClick={() => setMode(m)}
                  className="rounded-md px-3 py-1 text-[12px]"
                  style={mode === m
                    ? { background: "var(--panel)", color: "var(--accent)", border: "1px solid var(--line)" }
                    : { color: "var(--muted)", border: "1px solid transparent" }}>
                  {m === "single" ? "▶ single" : "⚔ arena"}
                </button>
              ))}
            </div>
          )}
          {activeDoc && mode === "single" && (
            <RunComposer
              models={models.length ? models : [{ id: "byo", label: "BYO-JSON (offline)", cloud: false }]}
              value={composer}
              onChange={setComposer}
              onRun={startRun}
              running={!!running}
              keySet={keySet}
              onSaveKey={saveKey}
            />
          )}
          {activeDoc && ARENA_ENABLED && mode === "arena" && (
            <div className="flex flex-col gap-2 border-b p-3.5" style={{ borderColor: "var(--line)" }}>
              <div className="text-[11px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
                models to compare (each: extraction + paperpin grounding + its own native boxes)
              </div>
              <div className="max-h-[130px] overflow-y-auto rounded-md border p-1.5"
                style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
                {models.filter((m) => m.cloud && !m.template).map((m) => (
                  <label key={m.id} className="flex cursor-pointer items-center gap-2 px-1 py-0.5 text-[12px]">
                    <input type="checkbox"
                      checked={arenaModels.includes(m.id)}
                      onChange={(e) => setArenaModels((cur) =>
                        e.target.checked ? [...cur, m.id] : cur.filter((x) => x !== m.id))} />
                    <span style={{ color: "var(--text)" }}>{m.label}</span>
                  </label>
                ))}
              </div>
              <div className="rounded-md px-2.5 py-1.5 text-[11.5px]"
                style={{ background: "var(--panel)", color: "var(--muted)" }}>
                ☁ sends the document to each selected provider
              </div>
              <button onClick={startArena}
                disabled={arenaRunning || arenaModels.length === 0}
                className="rounded-lg py-2 text-[13.5px] font-semibold transition-all disabled:opacity-40"
                style={{
                  background: arenaRunning ? "var(--panel)" : "var(--text)",
                  border: "1px solid var(--line)",
                  color: arenaRunning ? "var(--muted)" : "var(--panel)",
                }}>
                {arenaRunning ? "battling…" : `⚔ run arena (${arenaModels.length})`}
              </button>
            </div>
          )}
        </div>

        {/* main area */}
        {ARENA_ENABLED && activeDoc && mode === "arena" && activeArena ? (
          <ArenaView doc={activeDoc} arena={activeArena} />
        ) : activeDoc ? (
          <ResultView doc={activeDoc} result={activeRun?.result ?? null}
            running={!!running} runId={activeRun?.id}
            progress={activeRun?.progress ?? null}
            run={activeRun}
            onRepin={activeRun?.status === "done" ? repin : undefined} />
        ) : (
          <div className="paper flex flex-1 flex-col items-center justify-center gap-3"
            style={{ color: "var(--muted)" }}>
            <svg width="92" height="68" viewBox="0 0 92 68" fill="none" aria-hidden="true">
              <path d="M3 12 v50 a4 4 0 0 0 4 4 h78 a4 4 0 0 0 4-4 V20 a4 4 0 0 0-4-4 H42 l-6-9 H7 a4 4 0 0 0-4 4 z"
                stroke="var(--line)" strokeWidth="2" strokeDasharray="5 4" />
              <circle cx="46" cy="40" r="5" fill="var(--accent)" />
              <rect x="44.6" y="44" width="2.8" height="12" rx="1.4" fill="var(--text)" />
            </svg>
            <div className="text-[15px]" style={{ color: "var(--text)", fontWeight: 700 }}>
              drop an invoice to begin
            </div>
            <div className="text-[12px]">every value pinned to the pixel it came from — or flagged</div>
          </div>
        )}
      </div>
    </div>
  );
}

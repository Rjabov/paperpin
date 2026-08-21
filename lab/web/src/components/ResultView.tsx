import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Candidate, Diagnostic, DocumentMeta, FieldResult, GroundedResult, NativeBox, Run } from "../types";
import { ModelJourney } from "./ModelJourney";
import { type StageEvent } from "./PipelineRail";
import { STATUS_COLOR, STATUS_TAG } from "./StatusBits";
import { ZoomBar, useZoom } from "./Zoom";

// Diagnose is an internal debugging surface; it ships only in dev builds.
const DEV_TOOLS = import.meta.env.VITE_ARENA === "1";
const VIEWS: ("fields" | "diagnose" | "json")[] =
  DEV_TOOLS ? ["fields", "diagnose", "json"] : ["fields", "json"];

export function ResultView({ doc, result, running, onRepin, runId, progress, run }: {
  doc: DocumentMeta;
  result: GroundedResult | null;
  running: boolean;
  onRepin?: (fresh: boolean) => void;
  runId?: number | null;
  progress?: StageEvent[] | null;
  run?: Run | null;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"fields" | "diagnose" | "json">("fields");
  const [showBoxes, setShowBoxes] = useState(true);
  const [showModel, setShowModel] = useState(true);

  const nativeEntries = useMemo(() =>
    Object.entries(run?.native ?? {}).filter(
      ([n, b]) => n !== "_error" && b.xyxy && b.page != null),
    [run]);
  const [copied, setCopied] = useState(false);
  const [diag, setDiag] = useState<Diagnostic | null>(null);
  const [diagRunId, setDiagRunId] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const { zoom, zoomIn, zoomOut, reset, onWheel } = useZoom();

  const resultJson = useMemo(
    () => (result ? JSON.stringify(result, null, 1) : ""), [result]);

  const copyJson = () => {
    navigator.clipboard.writeText(resultJson).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  };

  const fields = useMemo(() => {
    if (!result) return [];
    return Object.values(result.fields).sort((a, b) => {
      const ay = a.bbox ? a.page! * 10 + a.bbox[1] : 99;
      const by = b.bbox ? b.page! * 10 + b.bbox[1] : 99;
      return ay - by;
    });
  }, [result]);

  const located = fields.filter((f) => f.status === "verified" || f.status === "low_confidence").length;
  const total = fields.filter((f) => f.status !== "not_present").length;
  const notFound = fields.filter((f) => f.status === "not_found");

  // keyboard j/k walk (§5.1)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "TEXTAREA" || (e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key !== "j" && e.key !== "k") return;
      const idx = fields.findIndex((f) => f.name === selected);
      const next = e.key === "j" ? Math.min(fields.length - 1, idx + 1) : Math.max(0, idx - 1);
      if (fields[next]) setSelected(fields[next].name);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fields, selected]);

  useEffect(() => {
    if (!selected) return;
    listRef.current
      ?.querySelector(`[data-field="${CSS.escape(selected)}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      {/* document canvas */}
      <div className="paper relative flex min-w-0 flex-1 flex-col items-center gap-5 overflow-auto p-6"
        onWheel={onWheel}>
        <div className="sticky top-0 z-10 flex items-center gap-2 self-stretch rounded-lg border p-1"
          style={{ background: "rgba(247,244,236,.94)", borderColor: "var(--line)" }}>
          {result && !running && (
            <>
              <button onClick={() => setShowBoxes((v) => !v)}
                title="where paperpin verified each value on the paper"
                className="rounded-sm px-2 py-0.5 text-[11px] transition-all"
                style={showBoxes
                  ? { background: "var(--panel)", border: "1.5px solid var(--text)",
                      color: "var(--text)" }
                  : { background: "transparent", border: "1.5px solid var(--line)",
                      color: "var(--muted)", opacity: 0.75 }}>
                ─ paperpin pins
              </button>
              {nativeEntries.length > 0 && (
                <button onClick={() => setShowModel((v) => !v)}
                  title="where the model itself claims each value is (its own boxes, unverified)"
                  className="rounded-sm px-2 py-0.5 text-[11px] transition-all"
                  style={showModel
                    ? { background: "var(--panel)", border: "1.5px dashed var(--accent2)",
                        color: "var(--accent2)" }
                    : { background: "transparent", border: "1.5px dashed var(--line)",
                        color: "var(--muted)", opacity: 0.75 }}>
                  ┄ model claims
                </button>
              )}
              {nativeEntries.length > 0 && showBoxes && showModel && (
                <span className="text-[10.5px]" style={{ color: "var(--muted)" }}>
                  solid = verified on paper · dashed = model's own claim
                </span>
              )}
            </>
          )}
          <span className="ml-auto">
            <ZoomBar zoom={zoom} zoomIn={zoomIn} zoomOut={zoomOut} reset={reset} sticky={false} />
          </span>
        </div>
        {doc.pages.map((pg) => (
          <PageCanvas
            key={pg.index}
            docId={doc.id}
            pageIndex={pg.index}
            pageCount={doc.pages.length}
            fields={showBoxes ? fields.filter((f) => f.page === pg.index && f.bbox) : []}
            native={showModel ? nativeEntries.filter(([, b]) => b.page === pg.index) : []}
            ambiguous={showBoxes ? fields
              .filter((f) => f.status === "ambiguous")
              .flatMap((f) => (f.candidates ?? [])
                .map((c, i) => [f, c, i] as [FieldResult, Candidate, number])
                // the primary bbox is already drawn as the field's own pin
                .filter(([, c]) => c.page === pg.index
                  && !(f.page === c.page && f.bbox && c.bbox.every((v, k) => v === f.bbox![k]))))
              : []}
            running={running}
            selected={selected}
            onSelect={setSelected}
            zoom={zoom}
          />
        ))}
      </div>

      {/* field table */}
      <div className="flex w-[400px] min-w-[330px] flex-col border-l"
        style={{ borderColor: "var(--line)", background: "var(--panel2)" }}>
        <div className="flex items-baseline gap-2.5 border-b px-4 py-3" style={{ borderColor: "var(--line)" }}>
          <span className="text-[22px] font-medium" style={{
            fontFamily: "var(--mono)", color: "var(--text)",
          }}>
            {running ? "…" : result ? `${located}/${total}` : "—"}
          </span>
          <span className="label">
            fields pinned
          </span>
          {result && !running && (
            <span className="ml-auto flex flex-wrap items-center justify-end gap-1 text-[11px]">
              {onRepin && (
                <span className="mr-1 flex gap-1">
                  <button onClick={() => onRepin(false)}
                    className="rounded-sm border px-2 py-0.5"
                    title="run the paperpin pipeline on these values — no model call, OCR from cache"
                    style={{ borderColor: "var(--line)", color: "var(--text)" }}>
                    re-pin
                  </button>
                  <button onClick={() => onRepin(true)}
                    className="rounded-sm border px-2 py-0.5"
                    title="cold start: full pipeline including OCR from scratch — watch it live"
                    style={{ borderColor: "var(--line)", color: "var(--muted)" }}>
                    cold
                  </button>
                </span>
              )}
              {VIEWS.map((v) => (
                <button key={v}
                  onClick={() => {
                    setView(v);
                    if (v === "diagnose" && runId && diagRunId !== runId) {
                      setDiag(null);
                      api.runDiagnostic(runId).then((d) => {
                        setDiag(d); setDiagRunId(runId);
                      }).catch(() => {});
                    }
                  }}
                  className="rounded px-2 py-0.5"
                  style={view === v
                    ? { background: "var(--panel)", color: "var(--accent)", border: "1px solid var(--line)" }
                    : { color: "var(--muted)", border: "1px solid transparent" }}>
                  {v}
                </button>
              ))}
            </span>
          )}
        </div>

        {/* Timeline A — model journey. Always visible once a run exists. */}
        {run && (
          <div className="border-b px-4 py-2"
            style={{ borderColor: "var(--line)", background: "var(--panel2)" }}>
            <ModelJourney
              model={run.model}
              status={run.status}
              events={progress}
              tokenUsage={run.token_usage}
              costUsd={run.cost_usd}
              costApprox={run.cost_approx}
              latencyMs={run.latency_ms}
              timings={run.timings}
              error={run.error}
            />
          </div>
        )}

        {notFound.length > 0 && !running && (
          <div className="m-2.5 flex items-start gap-2.5 rounded-sm border px-3 py-2 text-[12px]"
            style={{ borderColor: "rgba(160,74,58,.5)", background: "#f2e3de" }}>
            <span className="stamp mt-0.5 flex-none">not on paper</span>
            <span>
              <span style={{ color: "var(--bad)", fontWeight: 700 }}>
                the model asserted {notFound.length === 1 ? "a value" : `${notFound.length} values`} the document never says
              </span>{" "}
              {notFound.slice(0, 4).map((f) => (
                <code key={f.name} style={{ fontFamily: "var(--mono)", color: "var(--bad)" }}>
                  {f.name}={JSON.stringify(f.value)}{" "}
                </code>
              ))}
              {notFound.length > 4 && (
                <span style={{ color: "var(--bad)" }}>
                  +{notFound.length - 4} more — stamped below
                </span>
              )}
            </span>
          </div>
        )}

        {view === "diagnose" && result && !running ? (
          <DiagnoseView diag={diag} onSelect={setSelected} selected={selected} />
        ) : view === "json" && result && !running ? (
          <div className="relative min-h-0 flex-1">
            <button onClick={copyJson}
              className="absolute right-4 top-2 z-10 rounded-md border px-2.5 py-1 text-[11px]"
              style={{ background: "var(--panel)", borderColor: copied ? "var(--ok)" : "var(--line)",
                       color: copied ? "var(--ok)" : "var(--muted)" }}>
              {copied ? "✓ copied" : "copy json"}
            </button>
            <pre className="h-full overflow-auto p-3 text-[11px] leading-[1.45]"
              style={{ fontFamily: "var(--mono)", color: "var(--text)", margin: 0 }}>
              {resultJson}
            </pre>
          </div>
        ) : (
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-2">
          {running && (
            <div className="p-4 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
              reading → extracting → pinning → verifying
            </div>
          )}
          {!running && fields.map((f, i) => (
            <FieldRow key={f.name} field={f} index={i}
              selected={selected === f.name}
              onSelect={() => setSelected(f.name)} />
          ))}
          {!running && !fields.length && (
            <div className="p-4 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
              no run yet — pick a model and hit ▶ run
            </div>
          )}
        </div>
        )}
      </div>
    </div>
  );
}

const VERDICTS: Record<string, { label: string; color: string; blame: string }> = {
  aligner_miss: { label: "paperpin missed — our bug", color: "var(--bad)",
                  blame: "the text IS in the OCR output but the aligner failed to pin it. Fixable on our side." },
  matcher_gap: { label: "damaged text, not pinned", color: "var(--amb)",
                 blame: "OCR read most of the text but paperpin's matchers could not link it. A smarter fallback could rescue it." },
  ocr_miss: { label: "OCR never read it", color: "var(--np)",
              blame: "the value is not in the recognized text at all — an OCR limitation, not an alignment bug." },
  rescued: { label: "damaged but pinned", color: "var(--warn)",
             blame: "OCR read the text imperfectly and paperpin still pinned it (flagged low confidence)." },
  ambiguous: { label: "ambiguous", color: "var(--amb)",
               blame: "several equally plausible places — all candidates reported." },
  clean: { label: "clean", color: "var(--ok)", blame: "read exactly, pinned, verified." },
  "null": { label: "model returned null", color: "var(--np)", blame: "field absent per the model." },
};

function DiagnoseView({ diag, onSelect, selected }: {
  diag: Diagnostic | null;
  onSelect: (n: string) => void;
  selected: string | null;
}) {
  if (!diag) {
    return <div className="p-4 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
      analyzing…
    </div>;
  }
  const s = diag.summary;
  const order = ["aligner_miss", "matcher_gap", "ocr_miss", "rescued", "ambiguous", "clean", "null"];
  const groups = order
    .map((v) => ({ v, items: diag.fields.filter((f) => f.verdict === v) }))
    .filter((g) => g.items.length > 0);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 text-[12px]">
      <div className="mb-3 rounded-lg border p-3 leading-relaxed"
        style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div className="mb-1 text-[10.5px] uppercase tracking-widest" style={{ color: "var(--muted)" }}>
          assumption: the model's values are correct
        </div>
        <div>OCR read <b style={{ color: "var(--text)" }}>
          {Math.round((s.ocr_read_rate ?? 0) * 100)}%</b> of the values
          · of those, paperpin pinned <b style={{ color: "var(--ok)" }}>
          {Math.round((s.aligner_recall_on_readable ?? 0) * 100)}%</b></div>
        <div style={{ color: "var(--muted)" }}>
          {(s.aligner_miss ?? 0) > 0
            ? <>⚠ {String(s.aligner_miss)} field(s) are OUR misses — text was readable, pin failed.</>
            : <>zero aligner misses — every readable value was pinned.</>}
          {" "}{(s.ocr_miss ?? 0) ? `${s.ocr_miss} value(s) OCR never read (not today's fight).` : ""}
        </div>
      </div>
      {groups.map(({ v, items }) => (
        <div key={v} className="mb-2.5">
          <div className="mb-1 flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ background: VERDICTS[v].color }} />
            <b style={{ color: VERDICTS[v].color }}>{VERDICTS[v].label}</b>
            <span style={{ color: "var(--muted)" }}>({items.length})</span>
          </div>
          <div className="mb-1 pl-4 text-[11px]" style={{ color: "var(--muted)" }}>
            {VERDICTS[v].blame}
          </div>
          {items.map((f) => (
            <div key={f.name} onClick={() => onSelect(f.name)}
              className="ml-4 flex cursor-pointer items-baseline gap-2 rounded px-1.5 py-0.5"
              style={selected === f.name ? { background: "var(--panel)" } : undefined}>
              <span style={{ color: "var(--muted)" }}>{f.name}</span>
              <span className="min-w-0 flex-1 truncate" style={{ fontFamily: "var(--mono)" }}>
                {f.value === null ? "∅" : String(f.value)}
              </span>
              {f.read && f.read !== "exact" && f.read_ratio != null && (
                <span className="text-[10.5px]" style={{ color: "var(--muted)" }}>
                  read {Math.round(f.read_ratio * 100)}%
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function PageCanvas({ docId, pageIndex, pageCount, fields, native, ambiguous, running, selected, onSelect, zoom }: {
  docId: number;
  pageIndex: number;
  pageCount: number;
  fields: FieldResult[];
  native: [string, NativeBox][];
  ambiguous: [FieldResult, Candidate, number][];
  running: boolean;
  selected: string | null;
  onSelect: (name: string) => void;
  zoom: number;
}) {
  return (
    <div className="flex-none"
      style={{ width: `${zoom * 100}%`, maxWidth: zoom <= 1 ? "100%" : undefined }}>
      <div className="label mb-1">page {pageIndex + 1} of {pageCount}</div>
      <div className="relative overflow-hidden rounded-[2px] bg-white"
        style={{ border: "1px solid var(--line)" }}>
        <img
          src={api.pageUrl(docId, pageIndex, zoom > 1.6 ? 2400 : 1400)}
          alt={`page ${pageIndex + 1}`}
          className="block h-auto w-full"
          style={{ filter: "brightness(.97) contrast(1.01)" }}
          draggable={false}
        />
        {!running && fields.map((f, i) => (
          <motion.div
            key={f.name}
            data-box={f.name}
            className={`box-pin ${f.status}${selected === f.name ? " selected" : ""}`}
            style={{
              left: `${f.bbox![0] * 100}%`,
              top: `${f.bbox![1] * 100}%`,
              width: `${(f.bbox![2] - f.bbox![0]) * 100}%`,
              height: `${(f.bbox![3] - f.bbox![1]) * 100}%`,
            }}
            initial={{ opacity: 0, scale: 1.6 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.15 + Math.min(i, 20) * 0.05, type: "spring", stiffness: 320, damping: 20 }}
            onClick={() => onSelect(f.name)}
          >
            <span className="pin-label">{f.name}</span>
          </motion.div>
        ))}
        {/* every tied place an ambiguous field could be — the tie IS the result */}
        {!running && ambiguous.map(([f, c, i]) => (
          <div
            key={`amb-${f.name}-${i}`}
            className={`box-pin ambiguous candidate${selected === f.name ? " selected" : ""}`}
            style={{
              left: `${c.bbox[0] * 100}%`,
              top: `${c.bbox[1] * 100}%`,
              width: `${(c.bbox[2] - c.bbox[0]) * 100}%`,
              height: `${(c.bbox[3] - c.bbox[1]) * 100}%`,
            }}
            onClick={() => onSelect(f.name)}
          >
            <span className="pin-label">{f.name} · tie {i + 1}</span>
          </div>
        ))}
        {!running && native.map(([n, b]) => (
          <div
            key={`n-${n}`}
            className={`model-box${selected === n ? " selected" : ""}`}
            style={{
              left: `${b.xyxy![0] * 100}%`,
              top: `${b.xyxy![1] * 100}%`,
              width: `${(b.xyxy![2] - b.xyxy![0]) * 100}%`,
              height: `${(b.xyxy![3] - b.xyxy![1]) * 100}%`,
            }}
            onClick={() => onSelect(n)}
          >
            <span className="pin-label">model · {n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function FieldRow({ field, selected, onSelect }: {
  field: FieldResult;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const warn = field.notes.filter((n) => n.startsWith("⚠"));
  const dim = field.status === "not_present";
  return (
    <div
      data-field={field.name}
      onClick={onSelect}
      className="my-1 cursor-pointer rounded-sm border px-2.5 py-1.5"
      style={{
        background: dim ? "var(--panel2)" : "var(--panel)",
        borderColor: "var(--line)",
        borderStyle: dim ? "dashed" : "solid",
        borderLeft: dim ? undefined : `3px solid ${STATUS_COLOR[field.status]}`,
        outline: selected ? "2px solid var(--text)" : "none",
        outlineOffset: -1,
      }}
    >
      <div className="label" style={{ color: dim ? "var(--muted)" : "var(--text)" }}>
        {field.name}
      </div>
      <div className="break-words text-[13px]" style={{ fontFamily: "var(--mono)", color: dim ? "var(--muted)" : "var(--text)" }}>
        {field.value === null ? "—" : String(field.value)}
      </div>
      <div className="mt-0.5">
        {field.status === "not_found" ? (
          <span className="stamp">not found</span>
        ) : (
          <span className="text-[9.5px] font-bold uppercase" style={{
            fontFamily: "var(--display)", letterSpacing: "0.12em",
            color: STATUS_COLOR[field.status],
          }}>
            {STATUS_TAG[field.status]}
            {field.status === "ambiguous" && field.candidates?.length
              ? ` · ${field.candidates.length} tied places` : ""}
            {field.page != null ? ` · p.${field.page + 1}` : ""}
          </span>
        )}
      </div>
      {selected && field.evidence && (
        <div className="mt-1 text-[11px]" style={{ color: "var(--muted)" }}>
          match: “{field.evidence}”{field.anchor ? <> · anchor: <b>{field.anchor}</b></> : null}
          {field.method ? <> · via {field.method}</> : null}
        </div>
      )}
      {selected && warn.map((n) => (
        <div key={n} className="mt-0.5 text-[11px]" style={{ color: "var(--warn)" }}>{n}</div>
      ))}
    </div>
  );
}

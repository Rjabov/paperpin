import { useRef, useState } from "react";
import type { ModelOption } from "../types";

export interface ComposerValue {
  model: string;
  prompt: string;
  byoJson: string;
}

/** Two ways in, nothing else: run a cloud model on the document, or paste
 * an extraction you already have and let paperpin pin it. */
export function RunComposer({ models, value, onChange, onRun, running, keySet, onSaveKey }: {
  models: ModelOption[];
  value: ComposerValue;
  onChange: (v: ComposerValue) => void;
  onRun: () => void;
  running: boolean;
  keySet: boolean;
  onSaveKey: (key: string) => Promise<void>;
}) {
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const isByo = value.model === "byo";
  const cloudModels = models.filter((m) => m.cloud && !m.template);
  const lastCloud = useRef(cloudModels[0]?.id ?? "");
  if (!isByo && value.model) lastCloud.current = value.model;

  const setByo = (text: string) => {
    onChange({ ...value, byoJson: text });
    if (!text.trim()) { setJsonError(null); return; }
    try { JSON.parse(text); setJsonError(null); }
    catch (e) { setJsonError((e as Error).message); }
  };

  const saveKey = () => {
    if (!keyDraft.trim()) return;
    setKeyBusy(true);
    onSaveKey(keyDraft.trim()).then(() => setKeyDraft("")).finally(() => setKeyBusy(false));
  };

  const flow = (byo: boolean) =>
    onChange({ ...value, model: byo ? "byo" : (lastCloud.current || cloudModels[0]?.id || "byo") });

  const canRun = !running && (isByo ? !!value.byoJson.trim() && !jsonError : keySet);

  return (
    <div className="flex flex-col gap-2.5 border-t p-3.5" style={{ borderColor: "var(--line)" }}>
      {/* the two flows */}
      <div className="flex border-b" style={{ borderColor: "var(--line)" }}>
        {([["run model", false], ["paste output", true]] as const).map(([t, byo]) => (
          <button key={t} onClick={() => flow(byo)}
            className="label whitespace-nowrap px-2.5 py-1.5"
            style={isByo === byo
              ? { color: "var(--text)", boxShadow: "inset 0 -2px var(--accent)" }
              : { color: "var(--muted)" }}>
            {t}
          </button>
        ))}
      </div>

      {!isByo && !keySet && (
        <label className="label">
          gemini api key — stored locally, used only when you hit run
          <span className="mt-1 flex gap-1.5">
            <input
              type="password"
              placeholder="AIza…"
              className="w-full rounded-sm border px-2 py-1.5 text-[12.5px] outline-none"
              style={{ background: "var(--panel)", borderColor: "var(--line)",
                       color: "var(--text)", fontFamily: "var(--mono)" }}
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveKey(); }}
            />
            <button onClick={saveKey} disabled={keyBusy || !keyDraft.trim()}
              className="flex-none rounded-sm border px-2.5 text-[12px] disabled:opacity-40"
              style={{ borderColor: "var(--line)", color: "var(--text)" }}>
              {keyBusy ? "…" : "save"}
            </button>
          </span>
        </label>
      )}

      {!isByo && (
        <label className="label">
          model
          <select
            className="mt-1 w-full rounded-sm border px-2 py-1.5 text-[13px] outline-none"
            style={{ background: "var(--panel)", borderColor: "var(--line)", color: "var(--text)" }}
            value={value.model}
            onChange={(e) => onChange({ ...value, model: e.target.value })}
          >
            {cloudModels.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
        </label>
      )}

      {isByo ? (
        <label className="label">
          any model's json output — paperpin pins it, offline
          <textarea
            rows={5}
            spellCheck={false}
            placeholder='{"total": "146,14", "iban": "SK73 1100 …"}'
            className="mt-1 w-full resize-y rounded-sm border p-2 text-[12.5px] outline-none"
            style={{ background: "var(--panel)", borderColor: jsonError ? "var(--bad)" : "var(--line)",
                     color: "var(--text)", fontFamily: "var(--mono)" }}
            value={value.byoJson}
            onChange={(e) => setByo(e.target.value)}
          />
          {jsonError && <div className="mt-0.5 text-[11px] normal-case" style={{ color: "var(--bad)" }}>{jsonError}</div>}
        </label>
      ) : (
        <>
          <label className="label">
            extra prompt instructions <span className="normal-case">(optional)</span>
            <textarea
              rows={2}
              placeholder="e.g. dates exactly as printed; item names verbatim"
              className="mt-1 w-full resize-y rounded-sm border p-2 text-[12.5px] outline-none"
              style={{ background: "var(--panel)", borderColor: "var(--line)", color: "var(--text)" }}
              value={value.prompt}
              onChange={(e) => onChange({ ...value, prompt: e.target.value })}
            />
          </label>
          <div className="rounded-sm px-2.5 py-1.5 text-[11.5px]"
            style={{ background: "var(--panel)", color: "var(--muted)" }}>
            this run sends the document to Google — everything else stays on this machine
          </div>
        </>
      )}

      <button
        onClick={onRun}
        disabled={!canRun}
        className="rounded-sm py-2 text-[13.5px] font-semibold transition-all disabled:opacity-40"
        style={{
          background: running ? "var(--panel)" : "var(--text)",
          border: "1px solid var(--line)", color: running ? "var(--muted)" : "var(--panel)",
        }}
      >
        {running ? "pinning…" : "run"}
      </button>
    </div>
  );
}

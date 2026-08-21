import type { Arena, ArenaSummary, Diagnostic, DocumentMeta, ModelOption, Preset, Run } from "./types";

// The server hands out a per-start token in the URL it prints; every API
// call carries it back. Header for fetches, query param for <img>/<a> URLs.
const token = new URLSearchParams(window.location.search).get("token") ?? "";

const withToken = (url: string) =>
  token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;

function req(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("X-Lab-Token", token);
  return fetch(url, { ...init, headers });
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listDocuments: () => req("/api/documents").then((r) => json<DocumentMeta[]>(r)),

  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req("/api/documents", { method: "POST", body: form }).then((r) =>
      json<DocumentMeta>(r));
  },

  pageUrl: (docId: number, page: number, width = 1400) =>
    withToken(`/api/documents/${docId}/pages/${page}.jpg?width=${width}`),

  listModels: () => req("/api/models").then((r) => json<ModelOption[]>(r)),
  listPresets: () => req("/api/presets").then((r) => json<Preset[]>(r)),

  createRun: (body: {
    document_id: number;
    model: string;
    schema_spec?: Record<string, unknown> | null;
    prompt?: string | null;
    extraction?: Record<string, unknown> | null;
  }) => req("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => json<{ run_id: number; status: string }>(r)),

  getRun: (id: number) => req(`/api/runs/${id}`).then((r) => json<Run>(r)),
  repinRun: (id: number, fresh = false) =>
    req(`/api/runs/${id}/repin?fresh=${fresh}`, { method: "POST" }).then((r) =>
      json<{ run_id: number; status: string }>(r)),
  runDiagnostic: (id: number) =>
    req(`/api/runs/${id}/diagnostic`).then((r) => json<Diagnostic>(r)),
  listRuns: (docId: number) =>
    req(`/api/runs?document_id=${docId}`).then((r) => json<Run[]>(r)),

  settings: () => req("/api/settings").then((r) =>
    json<{ gemini_key_set: boolean; gemini_key_masked: string | null }>(r)),
  saveSettings: (geminiApiKey: string) => req("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gemini_api_key: geminiApiKey }),
  }).then((r) => json<{ gemini_key_set: boolean }>(r)),

  createArena: (body: {
    document_id: number;
    models: string[];
    schema_spec?: Record<string, unknown> | null;
    prompt?: string | null;
  }) => req("/api/arena", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => json<{ arena_id: number; status: string }>(r)),

  getArena: (id: number) => req(`/api/arena/${id}`).then((r) => json<Arena>(r)),
  listArenas: (docId: number) =>
    req(`/api/arenas?document_id=${docId}`).then((r) => json<ArenaSummary[]>(r)),
  arenaExportUrl: (id: number) => withToken(`/api/arena/${id}/export`),
  runExportUrl: (id: number) => withToken(`/api/runs/${id}/export`),
};

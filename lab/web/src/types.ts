export type Status = "verified" | "low_confidence" | "ambiguous" | "not_found" | "not_present";

export interface PageInfo {
  index: number;
  width: number;
  height: number;
  route: "textlayer" | "ocr";
}

export interface DocumentMeta {
  id: number;
  filename: string;
  sha256: string;
  pages: PageInfo[];
  created_at: number;
  warning?: string;
}

export interface Candidate {
  page: number;
  bbox: [number, number, number, number];
  score: number;
  evidence: string;
  anchor?: string | null;
}

export interface FieldResult {
  name: string;
  value: unknown;
  status: Status;
  confidence: number;
  page: number | null;
  bbox: [number, number, number, number] | null;
  evidence: string | null;
  method: string | null;
  anchor: string | null;
  quote: string | null;
  candidates: Candidate[];
  notes: string[];
}

export interface GroundedResult {
  source: string;
  pages: PageInfo[];
  fields: Record<string, FieldResult>;
  summary: Record<string, number>;
  meta?: Record<string, unknown>;
}

export interface Timings {
  extract_s?: number;
  ground_s?: number;
  native_s?: number;
}

export interface StageEventT {
  stage: string;
  start_s: number;
  end_s?: number;
  info?: Record<string, unknown>;
}

export interface Run {
  id: number;
  document_id: number;
  arena_id?: number | null;
  model: string;
  status: "queued" | "running" | "done" | "error";
  error: string | null;
  latency_ms: number | null;
  token_usage: { prompt_tokens: number; output_tokens: number } | null;
  timings?: Timings | null;
  progress?: StageEventT[] | null;
  cost_usd?: number | null;
  cost_approx?: boolean;
  created_at: number;
  result?: GroundedResult;
  /** the model's own box claims, keyed by field name ("_error" = failed pass) */
  native?: Record<string, NativeBox>;
}

export interface ModelOption {
  id: string;
  label: string;
  cloud: boolean;
  note?: string;
  template?: boolean;
}

export interface Preset {
  name: string;
  schema_spec: Record<string, unknown> | null;
  prompt_text?: string | null;
  builtin: boolean;
}

export interface NativeBox {
  page: number | null;
  value: unknown;
  raw: number[] | null;   // model-verbatim box_2d [ymin,xmin,ymax,xmax] 0-1000
  xyxy: [number, number, number, number] | null;
}

export interface ArenaScore {
  statuses: Record<string, number>;
  located: number;
  n_fields: number;
  native_boxes: number;
  mean_iou_vs_native: number | null;
  native_iou50_rate: number | null;
  /** native matches ANY of our reported locations, or the pinned evidence is
   *  the value itself — repeated prints are agreement, not error */
  native_agree_rate?: number | null;
  latency_ms: number | null;
  timings?: Timings | null;
  token_usage: { prompt_tokens: number; output_tokens: number } | null;
  cost_usd?: number | null;
  cost_approx?: boolean;
}

export interface ArenaEntry {
  run_id: number;
  model: string;
  status: string;
  error: string | null;
  progress?: StageEventT[] | null;
  result: GroundedResult | null;
  native: Record<string, NativeBox>;  // key "_error" marks a failed native pass
  score: ArenaScore | null;
}

export interface Arena {
  id: number;
  document_id: number;
  status: "queued" | "running" | "done" | "error";
  error: string | null;
  models: string[];
  created_at: number;
  entries: ArenaEntry[];
  agreement: Record<string, { values: Record<string, unknown>; all_agree: boolean }>;
}

export interface DiagnosticField {
  name: string;
  value: unknown;
  verdict: "clean" | "rescued" | "aligner_miss" | "matcher_gap" | "ocr_miss" | "null" | "ambiguous";
  read: "exact" | "partial" | "missing" | null;
  read_ratio?: number;
  status: string;
}

export interface Diagnostic {
  run_id: number;
  assumption: string;
  summary: Record<string, number | null> & {
    ocr_read_rate: number | null;
    aligner_recall_on_readable: number | null;
  };
  fields: DiagnosticField[];
}

export interface ArenaSummary {
  id: number;
  document_id: number;
  models: string[];
  status: string;
  created_at: number;
}

/**
 * Backend API client (task 10).
 *
 * Contracts mirror the verified backend shapes:
 * - POST /api/v1/chat                 → SSE stream (citations → token* → done)
 * - POST /api/v1/matters/{id}/analysis → {matter_id, analysis_id, kind, content}
 * - POST /api/v1/matters/{id}/drafts   → draft payload with review_state
 * - POST /api/v1/drafts/{id}/{acknowledge|lawyer_review|transition}
 * - POST /api/v1/matters/{id}/documents/upload (multipart)
 * - GET  /api/v1/library/coverage
 * - GET/POST /api/v1/search (domain: matter|authority|both, separate lists)
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Domain = "matter" | "authority";

export interface MatterCitation {
  domain: "matter";
  document_id: number;
  version_no: number;
  doc_type: string;
  page: number;
  span: [number, number];
  faithful_ref: string;
}

export interface AuthorityCitation {
  domain: "authority";
  source: string;
  version: string;
  edition: string;
  pub_date: string | null;
  doc_date: string | null;
  language: string;
  article_or_section: string;
}

export type Citation = MatterCitation | AuthorityCitation;

export type SseEvent =
  | { type: "citations"; citations: Citation[] }
  | { type: "token"; text: string }
  | { type: "done"; not_found?: boolean }
  | { type: "error"; code: string; detail: string };

/**
 * Parse one SSE `data:` payload line into a typed event.
 * Pure function — unit tested in citation-domain-badge + api tests.
 */
export function parseSseLine(line: string): SseEvent | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("data:")) return null;
  const payload = trimmed.slice(5).trim();
  if (!payload || payload === "[DONE]") return null;
  try {
    const obj = JSON.parse(payload) as SseEvent;
    if (typeof obj !== "object" || obj === null || !("type" in obj)) return null;
    return obj;
  } catch {
    return null;
  }
}

/**
 * Stream POST /api/v1/chat SSE events. Uses fetch + ReadableStream
 * (no EventSource — it cannot POST bodies). AbortController aborts cleanly.
 */
export async function* streamChat(
  matterId: number,
  content: string,
  opts: { signal?: AbortSignal; consent?: boolean } = {},
): AsyncGenerator<SseEvent, void, void> {
  const res = await fetch(`${API_BASE}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      matter_id: matterId,
      content,
      consent: opts.consent ?? false,
    }),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await safeText(res));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const event = parseSseLine(line);
        if (event) yield event;
      }
    }
    const tail = parseSseLine(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.status = status;
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return "request failed";
  }
}

/* ---------- REST helpers ---------- */

export interface AnalysisContent {
  status: "complete" | "needs-documents";
  parties: Array<{ name: string; kind: string; span_ref: SpanRef }>;
  dates: Array<{ label: string; value: string; span_ref: SpanRef }>;
  obligations: Array<{ who: string; what: string; span_ref: SpanRef }>;
  issues: Array<{
    issue: string;
    finding: string;
    risk: "High" | "Medium" | "Low";
    span_refs: SpanRef[];
  }>;
  gaps: string[];
  summary_ar: string;
  summary_fr: string;
  contradictions: Array<{ a: SpanRef; b: SpanRef; note: string }>;
}

export interface SpanRef {
  document_id: number;
  page: number;
  span: [number, number];
}

export interface AnalysisOut {
  matter_id: number;
  analysis_id: number;
  kind: string;
  content: AnalysisContent;
}

export async function runAnalysis(matterId: number): Promise<AnalysisOut> {
  const res = await fetch(`${API_BASE}/api/v1/matters/${matterId}/analysis`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<AnalysisOut>;
}

export interface DraftOut {
  matter_id: number;
  draft_id: number;
  draft_type: string;
  content: string;
  review_state: "draft" | "acknowledged" | "lawyer_reviewed";
  provisional_banner: string;
  provisional_banner_ar: string;
  provisional_banner_fr: string;
  status_label: string;
  citations: Citation[];
  reviewer: string | null;
  reviewed_at: string | null;
  polished: boolean;
}

export async function createDraft(
  matterId: number,
  draftType: "opinion" | "client_email" | "demand_letter" | "memo",
): Promise<DraftOut> {
  const res = await fetch(`${API_BASE}/api/v1/matters/${matterId}/drafts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft_type: draftType }),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<DraftOut>;
}

export async function acknowledgeDraft(draftId: number): Promise<DraftOut> {
  const res = await fetch(`${API_BASE}/api/v1/drafts/${draftId}/acknowledge`, {
    method: "POST",
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<DraftOut>;
}

export async function lawyerReviewDraft(
  draftId: number,
  reviewer: string,
): Promise<DraftOut> {
  const res = await fetch(`${API_BASE}/api/v1/drafts/${draftId}/lawyer_review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewer }),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<DraftOut>;
}

export interface UploadOut {
  matter_id: number;
  document_id: number;
  version_no: number;
  filename: string;
  doc_type: string;
  status: string;
  needs_review: boolean;
  blob_ref: string;
  indexed_count: number;
  error: string | null;
}

export async function uploadDocument(
  matterId: number,
  file: File,
  onProgress?: (loaded: number, total: number | null) => void,
): Promise<UploadOut> {
  // XMLHttpRequest gives upload progress; fetch does not (no dup Body stream).
  return new Promise<UploadOut>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/v1/matters/${matterId}/documents/upload`);
    xhr.upload.onprogress = (ev) =>
      onProgress?.(ev.loaded, ev.lengthComputable ? ev.total : null);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadOut);
        } catch (e) {
          reject(e instanceof Error ? e : new Error("invalid upload response"));
        }
      } else {
        reject(new ApiError(xhr.status, xhr.responseText));
      }
    };
    xhr.onerror = () => reject(new Error("upload network error"));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

export interface CoverageEntry {
  source: string;
  version: string;
  edition: string;
  pub_date?: string | null;
  doc_date?: string | null;
  hijri_date?: string | null;
  language?: string;
  coverage_note?: string | null;
  chunks?: number;
  status?: string;
}

export async function libraryCoverage(): Promise<{
  titles: CoverageEntry[];
  gaps: string[];
  library_version: string | null;
}> {
  const res = await fetch(`${API_BASE}/api/v1/library/coverage`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json();
}

export function matterRefLabel(c: MatterCitation): string {
  return `[matter: doc ${c.document_id} p.${c.page} ¶${c.span[0]}–${c.span[1]}]`;
}

export function authorityRefLabel(c: AuthorityCitation): string {
  return `[authority: ${c.article_or_section} v${c.version}]`;
}

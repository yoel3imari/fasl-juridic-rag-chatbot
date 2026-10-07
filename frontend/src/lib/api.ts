/**
 * Backend API client.
 *
 * Contracts mirror the verified backend shapes:
 * - POST /api/v1/chat                 → SSE stream (citations → token* → done)
 * - POST /api/v1/matters/{id}/analysis → {matter_id, analysis_id, kind, content}
 * - POST /api/v1/matters/{id}/drafts   → draft payload with review_state
 * - POST /api/v1/drafts/{id}/{acknowledge|lawyer_review|transition}
 * - POST /api/v1/matters/{id}/documents/upload (multipart)
 * - GET  /api/v1/library/coverage
 * - GET/POST /api/v1/search (domain: matter|authority|both, separate lists)
 * - GET/POST /api/v1/matters (list/create)
 * - GET/PATCH/DELETE /api/v1/matters/{id} (read/update/delete)
 * - GET/DELETE /api/v1/matters/{id}/documents[/{documentId}] (list/delete)
 */

export const API_BASE =
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
  /** Truncated cited text. Optional: absent on citations persisted before it shipped. */
  excerpt?: string;
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
  /** Truncated cited text. Optional: absent on citations persisted before it shipped. */
  excerpt?: string;
}

export type Citation = MatterCitation | AuthorityCitation;

export type SseEvent =
  | { type: "citations"; citations: Citation[] }
  | { type: "token"; text: string }
  | {
      type: "done";
      not_found?: boolean;
      conversation_id?: number;
      /** Present only on the legal-only refusal path. */
      out_of_scope?: boolean;
    }
  | { type: "error"; code: string; detail: string; conversation_id?: number }
  | { type: "status"; stage: string; message: string };

/**
 * Parse one SSE `data:` payload line into a typed event.
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
 * Stream POST /api/v1/chat SSE events. Uses fetch + ReadableStream.
 */
export async function* streamChat(
  matterId: number | null,
  content: string,
  opts: {
    conversationId?: number | null;
    signal?: AbortSignal;
    consent?: boolean;
    provider?: string;
    model?: string;
  } = {},
): AsyncGenerator<SseEvent, void, void> {
  const res = await fetch(`${API_BASE}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      matter_id: matterId ?? null,
      content,
      conversation_id: opts.conversationId ?? undefined,
      consent: opts.consent ?? false,
      provider: opts.provider,
      model: opts.model,
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

/* ---------- Matter helpers ---------- */

export interface Matter {
  id: number;
  title: string;
  matter_type: string;
  jurisdiction: string;
  language: string;
}

export interface MatterCreateInput {
  title: string;
  matter_type?: string;
  jurisdiction?: string;
  language?: string;
}

export interface MatterUpdateInput {
  title?: string;
  matter_type?: string;
  jurisdiction?: string;
  language?: string;
}

export interface MatterDeleteOut {
  status: string;
  id: number;
  removed_documents: number;
  removed_points: number;
  removed_files: number;
}

export async function listMatters(): Promise<Matter[]> {
  const res = await fetch(`${API_BASE}/api/v1/matters`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<Matter[]>;
}

export async function createMatter(input: MatterCreateInput): Promise<Matter> {
  const res = await fetch(`${API_BASE}/api/v1/matters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: input.title,
      matter_type: input.matter_type ?? "labor",
      jurisdiction: input.jurisdiction ?? "casablanca",
      language: input.language ?? "ar",
    }),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<Matter>;
}

export async function getMatter(matterId: number): Promise<Matter> {
  const res = await fetch(`${API_BASE}/api/v1/matters/${matterId}`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<Matter>;
}

export async function updateMatter(
  matterId: number,
  patch: MatterUpdateInput,
): Promise<Matter> {
  const res = await fetch(`${API_BASE}/api/v1/matters/${matterId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<Matter>;
}

export async function deleteMatter(
  matterId: number,
): Promise<MatterDeleteOut> {
  const res = await fetch(`${API_BASE}/api/v1/matters/${matterId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json();
}

/* ---------- Analysis helpers ---------- */

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

/* ---------- Drafts helpers ---------- */

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

/* ---------- Upload helpers ---------- */

export interface DocumentSection {
  section_id: string;
  parent_section_id: string | null;
  title: string;
  page_start: number;
  page_end: number;
  span_start: number;
  span_end: number;
  faithful_text: string;
  normalized_text: string;
  ocr_confidence: number | null;
  needs_review: boolean;
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
  sections?: DocumentSection[];
}

export interface DocumentDeleteOut {
  status: string;
  document_id: number;
  removed_points: number;
  removed_files: number;
}

export interface MatterDocument {
  document_id: number;
  original_name: string;
  filename: string;
  doc_type: string;
  status: string;
  needs_review: boolean;
  chunk_count: number;
  section_count: number;
  /** null when the document has no sections. */
  page_count: number | null;
  /** ISO datetime. */
  created_at: string;
}

export async function listMatterDocuments(
  matterId: number,
): Promise<MatterDocument[]> {
  const res = await fetch(`${API_BASE}/api/v1/matters/${matterId}/documents`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<MatterDocument[]>;
}

export async function deleteMatterDocument(
  matterId: number,
  documentId: number,
): Promise<DocumentDeleteOut> {
  const res = await fetch(
    `${API_BASE}/api/v1/matters/${matterId}/documents/${documentId}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json();
}

export async function uploadDocument(
  matterId: number,
  file: File,
  onProgress?: (loaded: number, total: number | null) => void,
): Promise<UploadOut> {
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

/* ---------- Library & Search helpers ---------- */

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

export interface CoverageSummaryTotals {
  files: number;
  indexed: number;
  extracted: number;
  embedded: number;
  chunks: number;
  chunks_indexed: number;
}

export interface CoverageSummary {
  totals: CoverageSummaryTotals;
  by_category: Record<string, number>;
  by_status: Record<string, Record<string, number>>;
  by_edition: Record<string, number>;
}

export interface CoverageResponse {
  titles: CoverageEntry[];
  gaps: string[];
  library_version: string | null;
  summary?: CoverageSummary;
  titles_truncated?: number;
  gaps_truncated?: number;
}

export async function libraryCoverage(): Promise<CoverageResponse> {
  const res = await fetch(`${API_BASE}/api/v1/library/coverage`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json();
}

export interface SearchResult {
  matter?: Array<{
    document_id: number;
    doc_type?: string;
    page: number;
    span: [number, number];
    text: string;
    faithful_ref?: string;
  }>;
  authority?: Array<{
    source: string;
    version: string;
    edition: string;
    article_or_section: string;
    text: string;
  }>;
}

export async function searchAll(
  query: string,
  matterId?: number | null,
  domain: "both" | "matter" | "authority" = "both",
): Promise<SearchResult> {
  const res = await fetch(`${API_BASE}/api/v1/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      domain,
      matter_id: matterId ?? undefined,
    }),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<SearchResult>;
}

export interface LibraryUploadInput {
  file: File;
  source: string;
  version: string;
  edition?: string;
  pub_date?: string;
  doc_date?: string;
  hijri_date?: string;
  language?: string;
  coverage_note?: string;
}

export interface LibraryUploadOut {
  status: string;
  source: string;
  version: string;
  edition: string;
  pub_date?: string | null;
  doc_date?: string | null;
  hijri_date?: string | null;
  language?: string | null;
  coverage_note?: string | null;
  chunks: number;
  embedded: number;
  message: string;
}

export async function uploadLibraryDocument(
  input: LibraryUploadInput,
  onProgress?: (loaded: number, total: number | null) => void,
): Promise<LibraryUploadOut> {
  return new Promise<LibraryUploadOut>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/v1/library/upload`);
    xhr.upload.onprogress = (ev) =>
      onProgress?.(ev.loaded, ev.lengthComputable ? ev.total : null);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as LibraryUploadOut);
        } catch (e) {
          reject(e instanceof Error ? e : new Error("invalid response"));
        }
      } else {
        reject(new ApiError(xhr.status, xhr.responseText));
      }
    };
    xhr.onerror = () => reject(new Error("upload network error"));

    const form = new FormData();
    form.append("file", input.file, input.file.name);
    form.append("source", input.source);
    form.append("version", input.version);
    form.append("edition", input.edition || "ar-general");
    if (input.pub_date) form.append("pub_date", input.pub_date);
    if (input.doc_date) form.append("doc_date", input.doc_date);
    if (input.hijri_date) form.append("hijri_date", input.hijri_date);
    if (input.language) form.append("language", input.language);
    if (input.coverage_note) form.append("coverage_note", input.coverage_note);

    xhr.send(form);
  });
}

/* ---------- Models & Providers helpers ---------- */

export interface ModelOption {
  id: string;
  name: string;
  description?: string | null;
  recommended?: boolean;
}

export interface ProviderOption {
  id: string;
  name: string;
  type: "local" | "cloud" | string;
  is_external: boolean;
  description: string;
  default_model: string;
  models: ModelOption[];
}

export interface ModelsResponse {
  current_provider: string;
  current_model: string;
  privacy_mode: string;
  providers: ProviderOption[];
}

export async function listChatModels(): Promise<ModelsResponse> {
  const res = await fetch(`${API_BASE}/api/v1/chat/models`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<ModelsResponse>;
}

/* ---------- LLM settings (persistence) ---------- */

export interface LlmSettings {
  current_provider: string;
  current_model: string;
  privacy_mode: string;
  keys_status: Record<string, boolean>;
  masked_keys: Record<string, string | null>;
  base_urls?: Record<string, string | null>;
}

export interface SaveLlmSettingsInput {
  provider?: string;
  model?: string;
  api_keys?: Record<string, string | null>;
  base_urls?: Record<string, string | null>;
}

export interface LlmTestInput {
  provider: string;
  model: string;
  base_url?: string;
  api_key?: string;
}

export interface LlmTestResult {
  success: boolean;
  message: string;
  latency_ms?: number | null;
}

export async function getLlmSettings(): Promise<LlmSettings> {
  const res = await fetch(`${API_BASE}/api/v1/settings/llm`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<LlmSettings>;
}

export async function saveLlmSettings(
  input: SaveLlmSettingsInput,
): Promise<LlmSettings> {
  const res = await fetch(`${API_BASE}/api/v1/settings/llm`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<LlmSettings>;
}

export async function testLlmConnection(
  input: LlmTestInput,
): Promise<LlmTestResult> {
  const res = await fetch(`${API_BASE}/api/v1/settings/llm/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<LlmTestResult>;
}

export interface MessageRecord {
  id: number;
  conversation_id: number;
  role: "user" | "assistant";
  content: string;
  citations_json?: Citation[] | null;
  created_at: string;
}

export interface ConversationSummary {
  id: number;
  matter_id: number | null;
  matter_title?: string | null;
  title: string;
  created_at: string;
  message_count: number;
  preview?: string | null;
}

export interface ConversationDetail {
  id: number;
  matter_id: number | null;
  matter_title?: string | null;
  title: string;
  created_at: string;
  messages: MessageRecord[];
}

export async function listConversations(
  matterId?: number | null,
  limit: number = 50,
): Promise<ConversationSummary[]> {
  const url = new URL(`${API_BASE}/api/v1/conversations`);
  if (matterId !== undefined && matterId !== null) {
    url.searchParams.set("matter_id", String(matterId));
  }
  url.searchParams.set("limit", String(limit));
  const res = await fetch(url.toString());
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<ConversationSummary[]>;
}

export async function getConversation(
  conversationId: number,
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/v1/conversations/${conversationId}`);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<ConversationDetail>;
}

export async function deleteConversation(
  conversationId: number,
): Promise<{ status: string; id: number }> {
  const res = await fetch(`${API_BASE}/api/v1/conversations/${conversationId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json();
}

export function matterRefLabel(c: MatterCitation): string {
  return `[matter: doc ${c.document_id} p.${c.page} ¶${c.span[0]}–${c.span[1]}]`;
}

export function authorityRefLabel(c: AuthorityCitation): string {
  return `[authority: ${c.article_or_section} v${c.version}]`;
}



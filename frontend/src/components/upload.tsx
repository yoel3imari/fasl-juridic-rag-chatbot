"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  ApiError,
  deleteMatterDocument,
  listMatterDocuments,
  uploadDocument,
  type MatterDocument,
  type UploadOut,
} from "@/lib/api";
import { useI18n, type Language } from "@/lib/i18n";
import {
  Upload as UploadIcon,
  FileText,
  AlertCircle,
  FileCheck,
  Layers,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const MAX_BYTES = 20_000_000; // 20 MB

type PanelError =
  | { source: "request"; message: string }
  | { source: "documentList" };

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return fallback;
}

function formatDate(iso: string, language: Language): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(language, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function rowFromUpload(out: UploadOut): MatterDocument {
  const sections = out.sections ?? [];
  return {
    document_id: out.document_id,
    original_name: out.filename,
    filename: out.filename,
    doc_type: out.doc_type,
    status: out.status,
    needs_review: out.needs_review,
    chunk_count: out.indexed_count,
    section_count: sections.length,
    page_count:
      sections.length > 0
        ? Math.max(...sections.map((section) => section.page_end))
        : null,
    created_at: new Date().toISOString(),
  };
}

function mergeRows(
  list: MatterDocument[],
  sessionUploads: Record<number, UploadOut>,
): MatterDocument[] {
  const known = new Set(list.map((doc) => doc.document_id));
  const pending = Object.values(sessionUploads)
    .filter((out) => !known.has(out.document_id))
    .map(rowFromUpload);
  return pending.length > 0 ? [...list, ...pending] : list;
}

export function Upload({
  matterId,
  onDocumentUploaded,
}: {
  matterId: number | null;
  onDocumentUploaded?: (doc: UploadOut) => void;
}) {
  const { t, language } = useI18n();
  const [progress, setProgress] = useState<number | null>(null);
  const [docs, setDocs] = useState<MatterDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<PanelError | null>(null);
  const [activeDocId, setActiveDocId] = useState<number | null>(null);
  const [sessionUploads, setSessionUploads] = useState<
    Record<number, UploadOut>
  >({});
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);

  const sessionUploadsRef = useRef<Record<number, UploadOut>>({});

  const rememberUpload = useCallback((out: UploadOut) => {
    sessionUploadsRef.current = {
      ...sessionUploadsRef.current,
      [out.document_id]: out,
    };
    setSessionUploads(sessionUploadsRef.current);
  }, []);

  const forgetUpload = useCallback((documentId: number) => {
    const { [documentId]: _removed, ...rest } = sessionUploadsRef.current;
    sessionUploadsRef.current = rest;
    setSessionUploads(rest);
  }, []);

  const loadDocuments = useCallback(async (id: number) => {
    const list = await listMatterDocuments(id);
    setDocs(mergeRows(list, sessionUploadsRef.current));
  }, []);

  useEffect(() => {
    sessionUploadsRef.current = {};
    setSessionUploads({});
    setActiveDocId(null);
    setPendingDeleteId(null);
    setDeleting(false);
    setError(null);
    if (matterId === null) {
      setDocs([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void listMatterDocuments(matterId)
      .then((list) => {
        if (cancelled) return;
        setDocs(mergeRows(list, sessionUploadsRef.current));
      })
      .catch(() => {
        if (cancelled) return;
        setDocs([]);
        setError({ source: "documentList" });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [matterId]);

  const onDrop = useCallback(
    async (files: File[]) => {
      const file = files[0];
      if (!file) return;
      if (matterId === null) {
        setError({ source: "request", message: t.upload.errorSelectMatter });
        return;
      }
      if (file.size > MAX_BYTES) {
        setError({
          source: "request",
          message: t.upload.errorTooLarge.replace(
            "{size}",
            String(Math.round(file.size / (1024 * 1024))),
          ),
        });
        return;
      }
      setError(null);
      setProgress(0);
      try {
        const out = await uploadDocument(matterId, file, (loaded, total) =>
          setProgress(total ? Math.round((loaded / total) * 100) : 0),
        );
        rememberUpload(out);
        setActiveDocId(out.document_id);
        onDocumentUploaded?.(out);
        try {
          await loadDocuments(matterId);
        } catch (refreshError) {
          setError({
            source: "request",
            message: toMessage(refreshError, t.upload.errorListFailed),
          });
        }
      } catch (e) {
        setError({ source: "request", message: toMessage(e, t.upload.errorFailed) });
      } finally {
        setProgress(null);
      }
    },
    [matterId, onDocumentUploaded, rememberUpload, loadDocuments, t],
  );

  const confirmDeleteDocument = useCallback(async () => {
    if (matterId === null || pendingDeleteId === null || deleting) return;
    const documentId = pendingDeleteId;
    setDeleting(true);
    setError(null);
    try {
      await deleteMatterDocument(matterId, documentId);
      setDocs((prev) =>
        prev.filter((doc) => doc.document_id !== documentId),
      );
      forgetUpload(documentId);
      setActiveDocId((prev) => (prev === documentId ? null : prev));
    } catch (err) {
      setError({ source: "request", message: toMessage(err, t.upload.deleteError) });
    } finally {
      setDeleting(false);
      setPendingDeleteId(null);
    }
  }, [matterId, pendingDeleteId, deleting, forgetUpload, t]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    multiple: false,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [
        ".docx",
      ],
      "text/plain": [".txt", ".md"],
      "text/markdown": [".md"],
    },
  });

  const pendingDeleteDoc =
    docs.find((doc) => doc.document_id === pendingDeleteId) ?? null;
  const pendingDeleteName = pendingDeleteDoc?.original_name ?? "";
  const errorMessage =
    error === null
      ? null
      : error.source === "documentList"
        ? t.upload.errorListFailed
        : error.message;

  return (
    <section aria-label="upload" className="space-y-4">
      {/* Upload Dropzone */}
      <div
        {...getRootProps()}
        className={`group relative flex flex-col items-center justify-center rounded-2xl border border-dashed p-6 text-center transition-all cursor-pointer shadow-2xs ${
          isDragActive
            ? "border-primary bg-primary/10"
            : "border-border/70 hover:border-primary/50 bg-background/40 hover:bg-muted/30"
        }`}
      >
        <input {...getInputProps()} />
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary mb-2.5 group-hover:scale-105 transition-transform shadow-2xs">
          <UploadIcon className="h-5 w-5" />
        </div>
        <p className="text-xs font-semibold text-foreground">
          {isDragActive
            ? t.upload.dropzoneActive
            : t.upload.dropzoneIdle}
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground/80">
          {t.upload.dropzoneHint}
        </p>
      </div>

      {/* Progress Bar */}
      {progress !== null && (
        <div className="space-y-1.5 animate-in fade-in">
          <div className="flex justify-between text-xs font-medium text-primary">
            <span>{t.upload.uploading}</span>
            <span>{progress}%</span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60"
          >
            <div
              className="h-full bg-primary transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Error Alert */}
      {errorMessage !== null && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium animate-in fade-in"
        >
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Documents List */}
      <div className="space-y-2 pt-1">
        <div className="flex items-center justify-between">
          <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
            <FileCheck className="h-3.5 w-3.5 text-primary" />
            <span>
              {t.upload.uploadedDocsHeading.replace(
                "{count}",
                String(docs.length),
              )}
            </span>
          </h4>
        </div>

        {loading && (
          <div
            role="status"
            aria-live="polite"
            className="rounded-xl border border-border/60 bg-background/40 p-4 text-center text-xs text-muted-foreground"
          >
            {t.upload.loadingDocs}
          </div>
        )}

        {!loading && matterId !== null && docs.length === 0 && (
          <p className="rounded-xl border border-border/60 bg-background/40 p-4 text-center text-xs text-muted-foreground">
            {t.upload.noDocsYet}
          </p>
        )}

        <div className="grid grid-cols-1 gap-2">
          {docs.map((doc) => {
            const sessionSections = sessionUploads[doc.document_id]?.sections;
            const statusLabels = t.upload.statusLabels;
            const statusText =
              statusLabels[doc.status as keyof typeof statusLabels] ?? doc.status;
            return (
              <Card
                key={doc.document_id}
                onClick={(event) => {
                  if (event.target === event.currentTarget) {
                    setActiveDocId(doc.document_id);
                  }
                }}
                className={`p-3 rounded-xl border transition-all ${
                  activeDocId === doc.document_id
                    ? "border-primary/60 bg-card shadow-2xs ring-1 ring-primary/30"
                    : "border-border/60 bg-background/40 hover:bg-muted/40 hover:border-border"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => setActiveDocId(doc.document_id)}
                    aria-pressed={activeDocId === doc.document_id}
                    aria-label={t.upload.selectDocument.replace(
                      "{name}",
                      doc.original_name,
                    )}
                    className="flex items-center gap-2.5 min-w-0 flex-1 text-start rounded-lg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring cursor-pointer"
                  >
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sky-500/10 text-sky-600 dark:text-sky-400">
                      <FileText className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 truncate">
                      <div className="truncate text-xs font-semibold text-foreground">
                        {doc.original_name}
                      </div>
                      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mt-0.5 flex-wrap">
                        <span>
                          {t.upload.docType}: {doc.doc_type}
                        </span>
                        <span>·</span>
                        <span>
                          {t.upload.sectionsCount.replace(
                            "{count}",
                            String(doc.section_count),
                          )}
                        </span>
                        <span>·</span>
                        <span>
                          {doc.page_count === null
                            ? "—"
                            : t.upload.pagesCount.replace(
                                "{count}",
                                String(doc.page_count),
                              )}
                        </span>
                        <span>·</span>
                        <span>
                          {t.upload.dateUploaded}:{" "}
                          {formatDate(doc.created_at, language)}
                        </span>
                      </div>
                    </div>
                  </button>

                  <div className="flex items-center gap-1.5 shrink-0">
                    <Badge
                      variant={doc.needs_review ? "risk-medium" : "matter"}
                      className="text-[10px] font-mono"
                    >
                      {statusText}
                    </Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={(event) => {
                        event.stopPropagation();
                        setPendingDeleteId(doc.document_id);
                      }}
                      disabled={deleting}
                      aria-label={t.upload.deleteButton.replace(
                        "{name}",
                        doc.original_name,
                      )}
                      className="h-7 w-7 rounded-lg text-destructive hover:bg-destructive/10 hover:text-destructive"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>

                {/* Parsed Sections Preview (documents uploaded in this session) */}
                {sessionSections && sessionSections.length > 0 && (
                  <div className="mt-2.5 pt-2 border-t border-border/50">
                    <span className="text-[11px] font-medium text-muted-foreground flex items-center gap-1 mb-1.5">
                      <Layers className="h-3 w-3" /> {t.upload.extractedSections}:
                    </span>
                    <div className="space-y-1.5 max-h-36 overflow-y-auto">
                      {sessionSections.map((sec, sIdx) => (
                        <div
                          key={sIdx}
                          className="rounded-lg bg-muted/40 p-2 text-xs border border-border/40"
                        >
                          <div className="font-semibold text-foreground text-[11px] mb-0.5">
                            {sec.title || `§ ${sIdx + 1}`} (p.{sec.page_start})
                          </div>
                          <div className="text-muted-foreground line-clamp-2 text-[11px] leading-relaxed">
                            {sec.faithful_text}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      </div>

      {/* Delete Document Confirmation */}
      <Dialog
        open={pendingDeleteId !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDeleteId(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <div className="flex items-center gap-2 text-destructive font-semibold text-base">
              <TriangleAlert className="h-5 w-5" />
              <span>{t.upload.deleteConfirmBadge}</span>
            </div>
            <DialogTitle className="text-xl font-bold">
              {t.upload.deleteConfirmTitle}
            </DialogTitle>
            <DialogDescription>
              {t.upload.deleteConfirmDesc.replace("{name}", pendingDeleteName)}
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <span>{t.upload.deleteConfirmIrreversible}</span>
          </div>

          <DialogFooter className="gap-2 pt-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => setPendingDeleteId(null)}
              disabled={deleting}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={confirmDeleteDocument}
              disabled={deleting}
            >
              {deleting ? t.upload.deletingButton : t.upload.deleteConfirmButton}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

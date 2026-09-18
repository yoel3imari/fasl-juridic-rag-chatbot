"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { ApiError, uploadDocument, type UploadOut } from "@/lib/api";
import {
  Upload as UploadIcon,
  FileText,
  CheckCircle2,
  AlertCircle,
  FileCheck,
  Layers,
  Sparkles,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const MAX_BYTES = 20_000_000; // 20 MB

export function Upload({
  matterId,
  onDocumentUploaded,
}: {
  matterId: number | null;
  onDocumentUploaded?: (doc: UploadOut) => void;
}) {
  const [progress, setProgress] = useState<number | null>(null);
  const [uploadedDocs, setUploadedDocs] = useState<UploadOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [activeDoc, setActiveDoc] = useState<UploadOut | null>(null);

  const onDrop = useCallback(
    async (files: File[]) => {
      const file = files[0];
      if (!file) return;
      if (matterId === null) {
        setError("اختر أو أنشئ قضية أولاً لرفع الوثيقة / Sélectionnez un dossier d'abord");
        return;
      }
      if (file.size > MAX_BYTES) {
        setError(
          `الملف كبير جداً (${Math.round(file.size / (1024 * 1024))} MB، الحد الأقصى 20 MB)`,
        );
        return;
      }
      setError(null);
      setProgress(0);
      try {
        const out = await uploadDocument(matterId, file, (loaded, total) =>
          setProgress(total ? Math.round((loaded / total) * 100) : 0),
        );
        setUploadedDocs((prev) => [out, ...prev]);
        setActiveDoc(out);
        onDocumentUploaded?.(out);
      } catch (e) {
        setError(
          e instanceof ApiError
            ? `${e.status}: ${e.message}`
            : e instanceof Error
              ? e.message
              : "فشل رفع الوثيقة",
        );
      } finally {
        setProgress(null);
      }
    },
    [matterId, onDocumentUploaded],
  );

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

  return (
    <section aria-label="upload" className="space-y-4">
      {/* Upload Dropzone */}
      <div
        {...getRootProps()}
        className={`group relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed p-6 text-center transition-all cursor-pointer ${
          isDragActive
            ? "border-primary bg-primary/10"
            : "border-border/80 hover:border-primary/60 bg-muted/20 hover:bg-muted/40"
        }`}
      >
        <input {...getInputProps()} />
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary mb-3 group-hover:scale-105 transition-transform">
          <UploadIcon className="h-6 w-6" />
        </div>
        <p className="text-sm font-semibold text-foreground">
          {isDragActive
            ? "أفلت الملف هنا للبدء في الفهرسة…"
            : "اسحب وثيقة القضية هنا، أو انقر للاختيار"}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          يدعم عقود العمل، الإنذارات، المراسلات، الأحكام (PDF, DOCX, TXT, MD حتى 20MB)
        </p>
      </div>

      {/* Progress Bar */}
      {progress !== null && (
        <div className="space-y-1.5 animate-in fade-in">
          <div className="flex justify-between text-xs font-semibold text-primary">
            <span>جارٍ رفع وفهرسة الوثيقة وتحليل الفقرات…</span>
            <span>{progress}%</span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-2 w-full overflow-hidden rounded-full bg-muted"
          >
            <div
              className="h-full bg-primary transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Error Alert */}
      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium"
        >
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Uploaded Documents List */}
      {uploadedDocs.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <FileCheck className="h-3.5 w-3.5 text-primary" />
              <span>الوثائق المفهرسة في هذا الملف ({uploadedDocs.length})</span>
            </h4>
          </div>

          <div className="grid grid-cols-1 gap-2">
            {uploadedDocs.map((doc, idx) => (
              <Card
                key={idx}
                onClick={() => setActiveDoc(doc)}
                className={`p-3.5 transition-all cursor-pointer ${
                  activeDoc?.document_id === doc.document_id
                    ? "border-primary ring-1 ring-primary/40 bg-card shadow-xs"
                    : "hover:bg-muted/40"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sky-500/10 text-sky-600 dark:text-sky-400">
                      <FileText className="h-5 w-5" />
                    </div>
                    <div className="min-w-0 truncate">
                      <div className="truncate text-sm font-semibold text-foreground">
                        {doc.filename}
                      </div>
                      <div className="flex items-center gap-2 text-[11px] text-muted-foreground mt-0.5">
                        <span>نوع: {doc.doc_type}</span>
                        <span>·</span>
                        <span>{doc.indexed_count} مقاطع مفهرسة</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    <Badge variant="matter" className="text-[10px]">
                      مفهرس بنجاح
                    </Badge>
                  </div>
                </div>

                {/* Parsed Sections Preview */}
                {doc.sections && doc.sections.length > 0 && (
                  <div className="mt-3 pt-2.5 border-t border-border/60">
                    <span className="text-[11px] font-semibold text-muted-foreground flex items-center gap-1 mb-1.5">
                      <Layers className="h-3 w-3" /> فقرات الوثيقة المستخرجة:
                    </span>
                    <div className="space-y-1.5 max-h-40 overflow-y-auto">
                      {doc.sections.map((sec, sIdx) => (
                        <div
                          key={sIdx}
                          className="rounded-lg bg-muted/50 p-2 text-xs border border-border/40"
                        >
                          <div className="font-semibold text-foreground text-[11px] mb-0.5">
                            {sec.title || `فقرة ${sIdx + 1}`} (ص.{sec.page_start})
                          </div>
                          <div className="text-muted-foreground line-clamp-2 text-[11px]">
                            {sec.faithful_text}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

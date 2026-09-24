"use client";

import * as React from "react";
import { useDropzone } from "react-dropzone";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { uploadLibraryDocument, type LibraryUploadOut, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  BookPlus,
  Upload,
  FileText,
  AlertCircle,
  Calendar,
  Globe,
  Tag,
  CheckCircle2,
  FileCheck,
} from "lucide-react";

const MAX_BYTES = 20_000_000; // 20 MB

export function UploadLibraryModal({
  open,
  onOpenChange,
  onDocumentUploaded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDocumentUploaded?: (out: LibraryUploadOut) => void;
}) {
  const { t, language: currentAppLanguage } = useI18n();
  const [file, setFile] = React.useState<File | null>(null);
  const [source, setSource] = React.useState("");
  const [version, setVersion] = React.useState("2024");
  const [edition, setEdition] = React.useState("ar-general");
  const [pubDate, setPubDate] = React.useState("");
  const [docDate, setDocDate] = React.useState("");
  const [hijriDate, setHijriDate] = React.useState("");
  const [language, setLanguage] = React.useState<string>(currentAppLanguage);
  const [coverageNote, setCoverageNote] = React.useState("");
  const [progress, setProgress] = React.useState<number | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [success, setSuccess] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setError(null);
      setSuccess(null);
      setProgress(null);
      setBusy(false);
    }
  }, [open]);

  const onDrop = React.useCallback(
    (acceptedFiles: File[]) => {
      const selected = acceptedFiles[0];
      if (!selected) return;
      if (selected.size > MAX_BYTES) {
        setError(
          t.upload.errorTooLarge.replace(
            "{size}",
            String(Math.round(selected.size / (1024 * 1024))),
          ),
        );
        return;
      }
      setError(null);
      setFile(selected);
      // Pre-fill source from filename if empty
      if (!source.trim()) {
        const cleanName = selected.name.replace(/\.[^/.]+$/, "").replace(/[_-]/g, " ");
        setSource(cleanName);
      }
    },
    [source, t],
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError(t.libraryUploadModal.errorSelectFile);
      return;
    }
    if (!source.trim() || !version.trim()) {
      setError(t.libraryUploadModal.errorMissingFields);
      return;
    }

    setBusy(true);
    setError(null);
    setSuccess(null);
    setProgress(0);

    try {
      const result = await uploadLibraryDocument(
        {
          file,
          source: source.trim(),
          version: version.trim(),
          edition,
          pub_date: pubDate.trim() || undefined,
          doc_date: docDate.trim() || undefined,
          hijri_date: hijriDate.trim() || undefined,
          language,
          coverage_note: coverageNote.trim() || undefined,
        },
        (loaded, total) => {
          setProgress(total ? Math.round((loaded / total) * 100) : 0);
        },
      );

      setSuccess(
        t.libraryUploadModal.successMessage.replace("{count}", String(result.chunks)),
      );
      onDocumentUploaded?.(result);

      // Reset form after short delay and close
      setTimeout(() => {
        setFile(null);
        setSource("");
        setCoverageNote("");
        setPubDate("");
        setDocDate("");
        setHijriDate("");
        onOpenChange(false);
      }, 1400);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : t.upload.errorFailed,
      );
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl sm:max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2 text-primary font-semibold text-xs uppercase tracking-wider">
            <BookPlus className="h-4 w-4" />
            <span>{t.libraryUploadModal.modalBadge}</span>
          </div>
          <DialogTitle className="text-lg sm:text-xl font-bold tracking-tight">
            {t.libraryUploadModal.modalTitle}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground leading-relaxed">
            {t.libraryUploadModal.modalDesc}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-1">
          {error && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium animate-in fade-in"
            >
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {success && (
            <div
              role="status"
              className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-600 dark:text-emerald-400 font-medium animate-in fade-in"
            >
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              <span>{success}</span>
            </div>
          )}

          {/* File Dropzone */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-foreground flex items-center justify-between">
              <span>{t.libraryUploadModal.fileLabel}</span>
              {file && (
                <Badge variant="outline" className="text-[10px] font-mono">
                  {Math.round(file.size / 1024)} KB
                </Badge>
              )}
            </label>

            <div
              {...getRootProps()}
              className={`group relative flex flex-col items-center justify-center rounded-xl border border-dashed p-4 text-center transition-all cursor-pointer shadow-2xs ${
                isDragActive
                  ? "border-primary bg-primary/10"
                  : file
                    ? "border-primary/60 bg-primary/5"
                    : "border-border/70 hover:border-primary/50 bg-background/50 hover:bg-muted/30"
              }`}
            >
              <input {...getInputProps()} />
              {file ? (
                <div className="flex items-center gap-2.5 text-start py-1">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <FileCheck className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-foreground truncate max-w-xs">
                      {file.name}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {t.libraryUploadModal.dropzoneHint}
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary mb-2 group-hover:scale-105 transition-transform">
                    <Upload className="h-4 w-4" />
                  </div>
                  <p className="text-xs font-semibold text-foreground">
                    {isDragActive
                      ? t.libraryUploadModal.dropzoneActive
                      : t.libraryUploadModal.dropzoneIdle}
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground/80">
                    {t.libraryUploadModal.dropzoneHint}
                  </p>
                </>
              )}
            </div>
          </div>

          {/* Progress bar */}
          {progress !== null && (
            <div className="space-y-1.5 animate-in fade-in">
              <div className="flex justify-between text-xs font-medium text-primary">
                <span>{t.libraryUploadModal.uploadingButton}</span>
                <span>{progress}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
                <div
                  className="h-full bg-primary transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          {/* Source Title & Version */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="sm:col-span-2 space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.libraryUploadModal.sourceLabel}</span>
              </label>
              <Input
                required
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder={t.libraryUploadModal.sourcePlaceholder}
                className="text-xs h-9 bg-background/80"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <Tag className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.libraryUploadModal.versionLabel}</span>
              </label>
              <Input
                required
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder={t.libraryUploadModal.versionPlaceholder}
                className="text-xs h-9 bg-background/80 font-mono"
              />
            </div>
          </div>

          {/* Edition & Language */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground">
                {t.libraryUploadModal.editionLabel}
              </label>
              <Select value={edition} onValueChange={setEdition}>
                <SelectTrigger className="h-9 text-xs bg-background/80">
                  <SelectValue placeholder="اختر نوع الطبعة" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ar-general" className="text-xs">
                    {t.libraryUploadModal.editionGeneralAr}
                  </SelectItem>
                  <SelectItem value="fr-translation" className="text-xs">
                    {t.libraryUploadModal.editionTranslationFr}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.libraryUploadModal.languageLabel}</span>
              </label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="h-9 text-xs bg-background/80">
                  <SelectValue placeholder="اختر اللغة" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ar" className="text-xs">
                    العربية (ar)
                  </SelectItem>
                  <SelectItem value="fr" className="text-xs">
                    Français (fr)
                  </SelectItem>
                  <SelectItem value="en" className="text-xs">
                    English (en)
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Dates (Pub date, Doc date, Hijri) */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.libraryUploadModal.pubDateLabel}</span>
              </label>
              <Input
                type="date"
                value={pubDate}
                onChange={(e) => setPubDate(e.target.value)}
                className="text-xs h-9 bg-background/80"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.libraryUploadModal.docDateLabel}</span>
              </label>
              <Input
                type="date"
                value={docDate}
                onChange={(e) => setDocDate(e.target.value)}
                className="text-xs h-9 bg-background/80"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground">
                {t.libraryUploadModal.hijriDateLabel}
              </label>
              <Input
                value={hijriDate}
                onChange={(e) => setHijriDate(e.target.value)}
                placeholder={t.libraryUploadModal.hijriDatePlaceholder}
                className="text-xs h-9 bg-background/80 font-mono"
              />
            </div>
          </div>

          {/* Coverage note */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-foreground">
              {t.libraryUploadModal.coverageNoteLabel}
            </label>
            <Textarea
              value={coverageNote}
              onChange={(e) => setCoverageNote(e.target.value)}
              placeholder={t.libraryUploadModal.coverageNotePlaceholder}
              rows={2}
              className="text-xs bg-background/80 resize-none"
            />
          </div>

          <DialogFooter className="gap-2 pt-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onOpenChange(false)}
              className="text-xs h-9 rounded-lg"
            >
              {t.libraryUploadModal.cancelButton}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={busy || !file || !source.trim() || !version.trim()}
              className="text-xs h-9 rounded-lg gap-1.5 font-medium shadow-2xs"
            >
              <Upload className="h-3.5 w-3.5" />
              <span>
                {busy
                  ? t.libraryUploadModal.uploadingButton
                  : t.libraryUploadModal.uploadButton}
              </span>
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

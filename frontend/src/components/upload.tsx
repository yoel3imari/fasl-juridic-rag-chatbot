"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { ApiError, uploadDocument, type UploadOut } from "@/lib/api";

const MAX_BYTES = 20_000_000; // mirrors backend MAX_UPLOAD_BYTES

export function Upload({ matterId }: { matterId: number | null }) {
  const [progress, setProgress] = useState<number | null>(null);
  const [result, setResult] = useState<UploadOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onDrop = useCallback(
    async (files: File[]) => {
      const file = files[0];
      if (!file) return;
      if (matterId === null) {
        setError("اختر قضية أولاً / Sélectionnez d'abord un dossier");
        return;
      }
      if (file.size > MAX_BYTES) {
        setError(
          `الملف كبير جداً (${file.size} بايت، الحد ${MAX_BYTES}) / Fichier trop volumineux`,
        );
        return;
      }
      setError(null);
      setResult(null);
      setProgress(0);
      try {
        const out = await uploadDocument(matterId, file, (loaded, total) =>
          setProgress(total ? Math.round((loaded / total) * 100) : 0),
        );
        setResult(out);
      } catch (e) {
        setError(
          e instanceof ApiError
            ? `${e.status}: ${e.message}`
            : e instanceof Error
              ? e.message
              : "upload failed",
        );
      } finally {
        setProgress(null);
      }
    },
    [matterId],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    multiple: false,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        [".docx"],
      "text/plain": [".txt", ".md"],
      "text/markdown": [".md"],
    },
  });

  return (
    <section aria-label="upload" className="flex flex-col gap-2">
      <div
        {...getRootProps()}
        className={`cursor-pointer rounded-lg border-2 border-dashed p-4 text-center text-sm ${
          isDragActive
            ? "border-sky-500 bg-sky-50 dark:bg-sky-950"
            : "border-neutral-300 dark:border-neutral-700"
        }`}
      >
        <input {...getInputProps()} />
        {isDragActive
          ? "أفلت الملف هنا / Déposez le fichier ici"
          : "اسحب وثيقة هنا أو انقر للاختيار (PDF/DOCX/TXT/MD) / Glissez un document ici"}
      </div>
      {progress !== null && (
        <div
          role="progressbar"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-2 overflow-hidden rounded bg-neutral-200 dark:bg-neutral-800"
        >
          <div className="h-full bg-sky-600" style={{ width: `${progress}%` }} />
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-500">
          {error}
        </p>
      )}
      {result && (
        <p className="text-sm text-green-700 dark:text-green-300">
          ✓ {result.filename} — {result.doc_type}
          {result.needs_review ? " (needs review)" : ""}
        </p>
      )}
    </section>
  );
}

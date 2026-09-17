"use client";

import { useEffect, useState } from "react";
import { ApiError, libraryCoverage, type CoverageEntry } from "@/lib/api";

export default function LibraryPage() {
  const [entries, setEntries] = useState<CoverageEntry[]>([]);
  const [gaps, setGaps] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    libraryCoverage()
      .then((cov) => {
        setEntries(cov.entries);
        setGaps(cov.gaps);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? `${e.status}: ${e.message}` : "load failed"),
      );
  }, []);

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
      <h1 className="text-2xl font-bold">
        المكتبة القانونية / Bibliothèque juridique
      </h1>
      {error && (
        <p role="alert" className="text-sm text-red-500">
          {error}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {entries.map((e, i) => (
          <li
            key={i}
            className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800"
          >
            <strong>{e.source}</strong> — {e.version} · {e.edition}
            {e.pub_date && <span> · pub {e.pub_date}</span>}
            {e.doc_date && <span> · doc {e.doc_date}</span>}
            {e.language && <span> · {e.language}</span>}
            {e.chunk_count !== undefined && (
              <span> · {e.chunk_count} passages</span>
            )}
          </li>
        ))}
        {entries.length === 0 && !error && (
          <li className="text-sm text-neutral-500">
            المكتبة فارغة بعد — / Bibliothèque vide pour le moment
          </li>
        )}
      </ul>
      {gaps.length > 0 && (
        <section>
          <h2 className="text-lg font-bold">النواقص / Lacunes</h2>
          <ul className="mt-1 list-disc ps-5 text-sm">
            {gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}

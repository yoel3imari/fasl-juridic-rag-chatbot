"use client";

import { useState } from "react";
import { ApiError, runAnalysis, type AnalysisOut } from "@/lib/api";

const RISK_STYLE: Record<string, string> = {
  High: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
  Medium:
    "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  Low: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-200",
};

export function AnalysisPanel({ matterId }: { matterId: number | null }) {
  const [data, setData] = useState<AnalysisOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (matterId === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      setData(await runAnalysis(matterId));
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `${e.status}: ${e.message}`
          : e instanceof Error
            ? e.message
            : "analysis failed",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="analysis" className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => void run()}
        disabled={matterId === null || busy}
        className="self-start rounded-md bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "…" : "تحليل / Analyser"}
      </button>
      {error && (
        <p role="alert" className="text-sm text-red-500">
          {error}
        </p>
      )}
      {data && data.content.status === "needs-documents" && (
        <p className="text-sm text-neutral-500">
          لا توجد وثائق بعد — ارفع وثائق أولاً / Aucun document — téléversez
          d&apos;abord
        </p>
      )}
      {data && data.content.status === "complete" && (
        <div className="flex flex-col gap-3">
          <div>
            <h3 className="text-sm font-bold">المخاطر / Risques</h3>
            <ul className="mt-1 flex flex-col gap-1">
              {data.content.issues.map((row, i) => (
                <li
                  key={i}
                  className="rounded-md border border-neutral-200 p-2 text-sm dark:border-neutral-800"
                >
                  <span
                    className={`me-2 rounded px-1.5 py-0.5 text-xs font-semibold ${RISK_STYLE[row.risk] ?? ""}`}
                  >
                    {row.risk}
                  </span>
                  <strong>{row.issue}</strong> — {row.finding}{" "}
                  <span className="text-xs text-neutral-500">
                    {row.span_refs
                      .map(
                        (r) =>
                          `[doc ${r.document_id} p.${r.page} ¶${r.span[0]}–${r.span[1]}]`,
                      )
                      .join(" ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-sm font-bold">النواقص / Lacunes</h3>
            <ul className="mt-1 list-disc ps-5 text-sm">
              {data.content.gaps.map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ul>
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer">
              ملخص مبسط / Résumé simplifié
            </summary>
            <p className="mt-1 whitespace-pre-line">{data.content.summary_ar}</p>
            <p className="mt-1 whitespace-pre-line">{data.content.summary_fr}</p>
          </details>
        </div>
      )}
    </section>
  );
}

"use client";

import { useState } from "react";
import {
  acknowledgeDraft,
  ApiError,
  createDraft,
  lawyerReviewDraft,
  type DraftOut,
} from "@/lib/api";
import { CitationDomainBadge } from "./citation-domain-badge";

const DRAFT_TYPES = ["opinion", "client_email", "demand_letter", "memo"] as const;

export function DraftsPanel({ matterId }: { matterId: number | null }) {
  const [draftType, setDraftType] =
    useState<(typeof DRAFT_TYPES)[number]>("opinion");
  const [draft, setDraft] = useState<DraftOut | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const guard = () => {
    if (matterId === null) {
      setError("اختر قضية أولاً / Sélectionnez d'abord un dossier");
      return false;
    }
    return true;
  };

  const wrap = async (fn: () => Promise<DraftOut>) => {
    if (!guard() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setDraft(await fn());
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `${e.status}: ${e.message}`
          : e instanceof Error
            ? e.message
            : "draft failed",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="drafts" className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <select
          aria-label="draft type"
          value={draftType}
          onChange={(e) =>
            setDraftType(e.target.value as (typeof DRAFT_TYPES)[number])
          }
          className="rounded-md border border-neutral-300 bg-transparent px-2 py-1.5 text-sm dark:border-neutral-700"
        >
          {DRAFT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void wrap(() => createDraft(matterId as number, draftType))
          }
          className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "…" : "إنشاء مسودة / Générer"}
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-500">
          {error}
        </p>
      )}
      {draft && (
        <article className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
          <div
            role="status"
            className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-sm dark:border-amber-800 dark:bg-amber-950"
          >
            {draft.provisional_banner} — {draft.status_label}
          </div>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-sm">
            {draft.content}
          </pre>
          {draft.citations.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {draft.citations.map((c, i) => (
                <CitationDomainBadge
                  key={`${c.domain}-${i}`}
                  citation={c}
                />
              ))}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            {draft.review_state === "draft" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void wrap(() => acknowledgeDraft(draft.draft_id))}
                className="rounded-md border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
              >
                إقرار / Accuser réception
              </button>
            )}
            {draft.review_state !== "lawyer_reviewed" && (
              <>
                <input
                  aria-label="reviewer"
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                  placeholder="Me. …"
                  className="rounded-md border border-neutral-300 bg-transparent px-2 py-1.5 dark:border-neutral-700"
                />
                <button
                  type="button"
                  disabled={busy || !reviewer.trim()}
                  onClick={() =>
                    void wrap(() =>
                      lawyerReviewDraft(draft.draft_id, reviewer.trim()),
                    )
                  }
                  className="rounded-md border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
                >
                  مراجعة المحامي / Révision avocat
                </button>
              </>
            )}
            {draft.review_state === "lawyer_reviewed" && (
              <span className="text-sm text-neutral-500">
                {draft.reviewer} · {draft.reviewed_at}
              </span>
            )}
          </div>
        </article>
      )}
    </section>
  );
}

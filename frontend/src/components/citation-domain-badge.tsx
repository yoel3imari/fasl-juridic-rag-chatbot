"use client";

import type { Citation } from "@/lib/api";
import { authorityRefLabel, matterRefLabel } from "@/lib/api";

interface Props {
  citation: Citation;
  /** Anchor id for click-to-source scroll targets. */
  anchorId?: string;
  onSelect?: (anchorId: string) => void;
}

/**
 * Claim-level citation card with a domain badge (task 10).
 *
 * Exactly one badge renders: [domain-badge-matter] or
 * [domain-badge-authority]. No relevance scores, no ranks — only
 * the human reference (doc/page/¶ or article/version/edition).
 */
export function CitationDomainBadge({ citation, anchorId, onSelect }: Props) {
  if (citation.domain === "matter") {
    return (
      <button
        type="button"
        data-claim-source={anchorId}
        onClick={() => anchorId && onSelect?.(anchorId)}
        className="ms-1 inline-flex cursor-pointer flex-col items-start gap-0.5 rounded-md border border-sky-300 bg-sky-50 px-2 py-1 text-start text-xs dark:border-sky-800 dark:bg-sky-950"
      >
        <span
          data-testid="domain-badge-matter"
          className="rounded bg-sky-600 px-1.5 py-0.5 font-semibold text-white"
        >
          matter
        </span>
        <span className="text-sky-900 dark:text-sky-200">
          {matterRefLabel(citation)}
        </span>
      </button>
    );
  }
  return (
    <button
      type="button"
      data-claim-source={anchorId}
      onClick={() => anchorId && onSelect?.(anchorId)}
      className="ms-1 inline-flex cursor-pointer flex-col items-start gap-0.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-start text-xs dark:border-amber-800 dark:bg-amber-950"
    >
      <span
        data-testid="domain-badge-authority"
        className="rounded bg-amber-600 px-1.5 py-0.5 font-semibold text-white"
      >
        authority
      </span>
      <span className="text-amber-900 dark:text-amber-200">
        {authorityRefLabel(citation)} · {citation.source} · {citation.edition}
      </span>
    </button>
  );
}

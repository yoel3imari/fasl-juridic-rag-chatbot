"use client";

import type { Citation } from "@/lib/api";
import { authorityRefLabel, matterRefLabel } from "@/lib/api";
import { BookOpen, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  citation: Citation;
  /** Anchor id for click-to-source scroll targets. */
  anchorId?: string;
  onSelect?: (anchorId: string) => void;
  className?: string;
}

/**
 * Claim-level citation card with a domain badge.
 *
 * Exactly one badge renders: [domain-badge-matter] or
 * [domain-badge-authority]. No relevance scores, no ranks — only
 * the human reference (doc/page/¶ or article/version/edition).
 */
export function CitationDomainBadge({
  citation,
  anchorId,
  onSelect,
  className,
}: Props) {
  if (citation.domain === "matter") {
    return (
      <button
        type="button"
        data-claim-source={anchorId}
        onClick={() => anchorId && onSelect?.(anchorId)}
        className={cn(
          "group my-0.5 inline-flex cursor-pointer flex-col items-start gap-1 rounded-lg border border-sky-400/30 bg-sky-500/10 hover:bg-sky-500/15 px-2.5 py-1.5 text-start text-xs transition-all shadow-xs dark:border-sky-500/20 dark:bg-sky-500/10 dark:hover:bg-sky-500/20",
          className,
        )}
      >
        <div className="flex items-center gap-1.5">
          <FileText className="h-3 w-3 text-sky-600 dark:text-sky-400" />
          <span
            data-testid="domain-badge-matter"
            className="rounded bg-sky-600 px-1.5 py-0.2 text-[10px] font-bold uppercase tracking-wider text-white"
          >
            matter
          </span>
        </div>
        <span className="font-mono text-[11px] text-sky-900 dark:text-sky-200">
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
      className={cn(
        "group my-0.5 inline-flex cursor-pointer flex-col items-start gap-1 rounded-lg border border-amber-400/30 bg-amber-500/10 hover:bg-amber-500/15 px-2.5 py-1.5 text-start text-xs transition-all shadow-xs dark:border-amber-500/20 dark:bg-amber-500/10 dark:hover:bg-amber-500/20",
        className,
      )}
    >
      <div className="flex items-center gap-1.5">
        <BookOpen className="h-3 w-3 text-amber-600 dark:text-amber-400" />
        <span
          data-testid="domain-badge-authority"
          className="rounded bg-amber-600 px-1.5 py-0.2 text-[10px] font-bold uppercase tracking-wider text-white"
        >
          authority
        </span>
      </div>
      <span className="font-mono text-[11px] text-amber-900 dark:text-amber-200">
        {authorityRefLabel(citation)} · {citation.source} · {citation.edition}
      </span>
    </button>
  );
}

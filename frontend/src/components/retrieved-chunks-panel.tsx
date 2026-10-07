"use client";

import { useState } from "react";
import {
  authorityRefLabel,
  matterRefLabel,
  type Citation,
  type Domain,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BookOpen, ChevronDown, Database, FileText, Maximize2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface RetrievedChunksPanelProps {
  chunks: Citation[];
  onSelect?: (citation: Citation) => void;
}

/** I18n label caption paired with the value it carries. */
type Meta = Array<[string, string]>;
type Copy = ReturnType<typeof useI18n>["t"]["retrievedChunks"];

const DOMAIN_ACCENT: Record<Domain, string> = {
  matter: "text-sky-600 dark:text-sky-400",
  authority: "text-amber-600 dark:text-amber-400",
};

const DISCLOSURE_ROW = "flex w-full cursor-pointer items-center gap-2 text-start transition-colors";
const CHEVRON = "ms-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200";
const META_CELL = "min-w-0 rounded-lg border border-border/30 bg-background/60 p-1.5";
const LINK_BUTTON = "cursor-pointer text-[10px] font-medium text-primary hover:underline";

/** Stable identity per chunk so expanding one card never collapses its siblings. */
function chunkKey(c: Citation, i: number): string {
  return c.domain === "matter"
    ? `m-${c.document_id}-${c.page}-${c.span[0]}-${c.span[1]}-${i}`
    : `a-${c.source}-${c.version}-${c.article_or_section}-${i}`;
}

/** Optional on the wire: citations persisted before `excerpt` shipped lack it. */
function excerptOf(c: Citation): string | null {
  const raw = c.excerpt;
  return raw !== undefined && raw.trim() !== "" ? raw : null;
}

/** Labels carry their value inline; the cell splits that into caption + value. */
function metaCell(template: string, value: string): [string, string] {
  const caption = template.replace(/\{[^}]*\}/g, "").replace(/[\s:·–-]+$/, "").trim();
  return [caption, value];
}

/** Derives a fresh Set so a toggle never mutates the one held in state. */
function toggleIn<T>(current: Set<T>, key: T): Set<T> {
  const next = new Set(current);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

/** The chevron mirrors with the row, so RTL turns it the opposite way. */
function chevronTurn(open: boolean, isRTL: boolean): string {
  if (!open) return "rotate-0";
  return isRTL ? "-rotate-180" : "rotate-180";
}

function refTextOf(c: Citation): string {
  if (c.domain === "matter") return matterRefLabel(c);
  return `${authorityRefLabel(c)} · ${c.source} · ${c.edition}`;
}

function metaOf(c: Citation, copy: Copy): Meta {
  if (c.domain === "matter") {
    return [
      metaCell(copy.metaDoc, String(c.document_id)),
      metaCell(copy.metaPage, String(c.page)),
      metaCell(copy.metaSpan, `${c.span[0]}–${c.span[1]}`),
      metaCell(copy.metaFaithfulRef, c.faithful_ref),
      metaCell(copy.metaDocType, c.doc_type),
    ];
  }
  return [
    metaCell(copy.metaSource, c.source),
    metaCell(copy.metaVersion, c.version),
    metaCell(copy.metaEdition, c.edition),
    metaCell(copy.metaLanguage, c.language),
    metaCell(copy.metaArticle, c.article_or_section),
  ];
}

interface ChunkCardProps {
  citation: Citation;
  /** Position inside its domain group: numbering restarts per group. */
  index: number;
  open: boolean;
  onToggle: (key: string) => void;
  onSelect?: (citation: Citation) => void;
}

function ChunkCard({ citation, index, open, onToggle, onSelect }: ChunkCardProps) {
  const { t, isRTL } = useI18n();
  const key = chunkKey(citation, index);
  const bodyId = `retrieved-chunk-body-${key}`;
  const accent = DOMAIN_ACCENT[citation.domain];
  const ChunkIcon = citation.domain === "matter" ? FileText : BookOpen;
  const excerpt = excerptOf(citation);

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-muted/20 shadow-2xs">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => onToggle(key)}
        className={cn(DISCLOSURE_ROW, "px-2.5 py-2 hover:bg-muted/50")}
      >
        <span className="shrink-0 text-[10px] font-semibold tabular-nums text-primary">{index + 1}.</span>
        <ChunkIcon className={cn("h-3 w-3 shrink-0", accent)} />
        <span className={cn("truncate font-mono text-[11px]", accent)}>{refTextOf(citation)}</span>
        <ChevronDown className={cn(CHEVRON, chevronTurn(open, isRTL))} />
      </button>

      {open && (
        <div id={bodyId} className="animate-in fade-in duration-200 space-y-2 border-t border-border/50 p-2.5">
          {excerpt === null ? (
            <p className="text-[11px] text-muted-foreground">{t.retrievedChunks.noExcerpt}</p>
          ) : (
            <p
              dir="auto"
              className="whitespace-pre-wrap break-words rounded-lg border border-border/40 bg-background/60 p-2 text-[11px] leading-relaxed text-foreground"
            >
              {excerpt}
            </p>
          )}

          <div className="grid grid-cols-2 gap-1.5 text-[10px]">
            {metaOf(citation, t.retrievedChunks).map(([caption, value]) => (
              <div key={caption} className={META_CELL}>
                <span className="block text-muted-foreground">{caption}</span>
                <span dir="auto" className="block truncate font-semibold text-foreground">{value}</span>
              </div>
            ))}
          </div>

          {onSelect && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onSelect(citation)}
              className="h-6 gap-1 rounded-lg border-border/60 bg-background/60 px-2 text-[10px] shadow-2xs"
            >
              <Maximize2 className="h-3 w-3" />
              <span>{t.retrievedChunks.viewDetails}</span>
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export function RetrievedChunksPanel({ chunks, onSelect }: RetrievedChunksPanelProps) {
  const { t, isRTL } = useI18n();
  // Groups start open: the chunks are the primary content of this panel.
  const [openGroups, setOpenGroups] = useState<Set<Domain>>(() => new Set<Domain>(["matter", "authority"]));
  const [openChunks, setOpenChunks] = useState<Set<string>>(() => new Set());

  const groups: Array<{ domain: Domain; label: string; items: Citation[] }> = [
    { domain: "matter", label: t.retrievedChunks.matterGroup, items: [] },
    { domain: "authority", label: t.retrievedChunks.authorityGroup, items: [] },
  ];
  for (const chunk of chunks) {
    const group = groups.find((g) => g.domain === chunk.domain);
    if (group) group.items.push(chunk);
  }
  const visibleGroups = groups.filter((g) => g.items.length > 0);
  const allKeys = visibleGroups.flatMap((g) => g.items.map((c, i) => chunkKey(c, i)));

  const toggleGroup = (domain: Domain) => setOpenGroups((prev) => toggleIn(prev, domain));
  const toggleChunk = (key: string) => setOpenChunks((prev) => toggleIn(prev, key));
  // A collapsed group hides its cards entirely, so both controls touch groups
  // too — otherwise "expand all" would look like it did nothing.
  const expandAll = () => {
    setOpenGroups(new Set(visibleGroups.map((g) => g.domain)));
    setOpenChunks(new Set(allKeys));
  };
  const collapseAll = () => {
    setOpenGroups(new Set());
    setOpenChunks(new Set());
  };

  return (
    <section aria-label={t.retrievedChunks.title} className="rounded-2xl border border-border/70 bg-card p-3 shadow-2xs">
      <div className="flex items-center justify-between gap-2 border-b border-border/50 pb-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <Database className="h-4 w-4 shrink-0 text-primary" />
          <h3 className="truncate text-xs font-semibold text-foreground">{t.retrievedChunks.title}</h3>
          <Badge
            variant="outline"
            className="shrink-0 border-primary/30 bg-primary/10 px-1.5 py-0 text-[10px] font-medium text-primary"
          >
            {chunks.length}
          </Badge>
        </div>

        {chunks.length > 0 && (
          <div className="flex shrink-0 items-center gap-2">
            <button type="button" onClick={expandAll} className={LINK_BUTTON}>
              {t.retrievedChunks.expandAll}
            </button>
            <span aria-hidden="true" className="text-[10px] text-muted-foreground/50">·</span>
            <button type="button" onClick={collapseAll} className={LINK_BUTTON}>
              {t.retrievedChunks.collapseAll}
            </button>
          </div>
        )}
      </div>

      {chunks.length === 0 ? (
        <p className="mt-2.5 text-[11px] text-muted-foreground">{t.retrievedChunks.empty}</p>
      ) : (
        <div className="mt-2.5 space-y-2.5">
          {visibleGroups.map((group) => {
            const GroupIcon = group.domain === "matter" ? FileText : BookOpen;
            const groupOpen = openGroups.has(group.domain);
            const groupId = `retrieved-chunks-group-${group.domain}`;

            return (
              <div key={group.domain}>
                <button
                  type="button"
                  aria-expanded={groupOpen}
                  aria-controls={groupId}
                  onClick={() => toggleGroup(group.domain)}
                  className={cn(DISCLOSURE_ROW, "rounded-xl border border-border/60 bg-muted/30 px-2.5 py-1.5 hover:bg-muted/60")}
                >
                  <GroupIcon className={cn("h-3.5 w-3.5 shrink-0", DOMAIN_ACCENT[group.domain])} />
                  <span className="truncate text-[11px] font-semibold text-foreground">{group.label}</span>
                  <Badge variant={group.domain} className="shrink-0 px-1.5 py-0 text-[10px]">
                    {group.items.length}
                  </Badge>
                  <ChevronDown className={cn(CHEVRON, chevronTurn(groupOpen, isRTL))} />
                </button>

                {groupOpen && (
                  <div id={groupId} className="mt-1.5 space-y-1.5">
                    {group.items.map((citation, i) => (
                      <ChunkCard key={chunkKey(citation, i)} citation={citation} index={i} open={openChunks.has(chunkKey(citation, i))} onToggle={toggleChunk} onSelect={onSelect} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

"use client";

import type { Citation, SpanRef } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { BookOpen, FileText, Scale, Copy, Check, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useState } from "react";

export function SourceInspector({
  activeCitation,
  activeSpan,
}: {
  activeCitation: Citation | null;
  activeSpan: SpanRef | null;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  if (!activeCitation && !activeSpan) {
    return (
      <div className="flex h-56 flex-col items-center justify-center rounded-2xl border border-dashed border-border/70 p-6 text-center bg-background/30 shadow-2xs">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-muted/60 text-muted-foreground mb-2.5">
          <BookOpen className="h-5 w-5" />
        </div>
        <h4 className="text-xs font-semibold text-foreground">
          {t.sourceInspector.placeholderTitle}
        </h4>
        <p className="mt-1 max-w-xs text-[11px] text-muted-foreground/80 leading-relaxed">
          {t.sourceInspector.placeholderSubtitle}
        </p>
      </div>
    );
  }

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (activeCitation) {
    const isMatter = activeCitation.domain === "matter";

    return (
      <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3.5 shadow-2xs animate-in fade-in duration-200">
        <div className="flex items-center justify-between pb-3 border-b border-border/50">
          <div className="flex items-center gap-2">
            <Badge variant={isMatter ? "matter" : "authority"} className="text-[10px] font-medium">
              {isMatter ? t.sourceInspector.matterBadge : t.sourceInspector.authorityBadge}
            </Badge>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              handleCopy(
                isMatter
                  ? activeCitation.faithful_ref
                  : `${activeCitation.source} - ${activeCitation.article_or_section}`,
              )
            }
            className="h-7 text-xs gap-1 rounded-lg border-border/60 bg-background/50 hover:bg-muted/50 shadow-2xs"
          >
            {copied ? (
              <>
                <Check className="h-3 w-3 text-emerald-500" />
                <span className="text-emerald-500 font-medium">{t.common.copied}</span>
              </>
            ) : (
              <>
                <Copy className="h-3 w-3" />
                <span>{t.sourceInspector.copyReference}</span>
              </>
            )}
          </Button>
        </div>

        <div className="text-sm font-semibold text-foreground flex items-center gap-2">
          {isMatter ? (
            <>
              <FileText className="h-4 w-4 text-sky-500 shrink-0" />
              <span>
                {t.sourceInspector.docNumber
                  .replace("{id}", String(activeCitation.document_id))
                  .replace("{docType}", activeCitation.doc_type)}
              </span>
            </>
          ) : (
            <>
              <Scale className="h-4 w-4 text-amber-500 shrink-0" />
              <span>
                {t.sourceInspector.legalCode
                  .replace("{source}", activeCitation.source)
                  .replace("{article}", activeCitation.article_or_section)}
              </span>
            </>
          )}
        </div>

        {isMatter ? (
          <div className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-muted/30 p-2 border border-border/30">
                <span className="text-muted-foreground block text-[10px]">Page:</span>
                <span className="font-semibold text-foreground text-xs">p. {activeCitation.page}</span>
              </div>
              <div className="rounded-lg bg-muted/30 p-2 border border-border/30">
                <span className="text-muted-foreground block text-[10px]">Paragraph:</span>
                <span className="font-semibold text-foreground font-mono text-xs">
                  ¶{activeCitation.span[0]} – {activeCitation.span[1]}
                </span>
              </div>
            </div>

            <div className="rounded-xl border border-sky-500/20 bg-sky-500/5 p-3 text-xs text-foreground leading-relaxed">
              <div className="text-[10px] font-bold text-sky-700 dark:text-sky-300 uppercase mb-1 flex items-center gap-1">
                <ShieldCheck className="h-3 w-3" /> {t.sourceInspector.sourceDetails}:
              </div>
              <div className="font-mono text-xs">{activeCitation.faithful_ref}</div>
            </div>
          </div>
        ) : (
          <div className="space-y-2.5">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded-lg bg-muted/30 p-2 border border-border/30">
                <span className="text-muted-foreground block text-[10px]">Version:</span>
                <span className="font-semibold text-foreground text-xs">{activeCitation.version}</span>
              </div>
              <div className="rounded-lg bg-muted/30 p-2 border border-border/30">
                <span className="text-muted-foreground block text-[10px]">Edition:</span>
                <span className="font-semibold text-foreground text-xs">{activeCitation.edition}</span>
              </div>
              <div className="rounded-lg bg-muted/30 p-2 border border-border/30">
                <span className="text-muted-foreground block text-[10px]">Language:</span>
                <span className="font-semibold text-foreground text-xs">{activeCitation.language}</span>
              </div>
            </div>

            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-foreground leading-relaxed">
              <div className="text-[10px] font-bold text-amber-700 dark:text-amber-300 uppercase mb-1 flex items-center gap-1">
                <ShieldCheck className="h-3 w-3" /> {activeCitation.source}
              </div>
              <div className="font-semibold text-foreground mb-1">
                {activeCitation.article_or_section}
              </div>
              <div className="text-muted-foreground text-[11px]">
                {t.sourceInspector.versionLabel.replace("{version}", activeCitation.version)} · {t.sourceInspector.editionLabel.replace("{edition}", activeCitation.edition)}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (activeSpan) {
    return (
      <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3 shadow-2xs animate-in fade-in duration-200">
        <div className="flex items-center justify-between pb-2 border-b border-border/50">
          <Badge variant="matter" className="text-[10px] font-medium">{t.sourceInspector.matterBadge}</Badge>
        </div>
        <div className="text-xs font-semibold text-foreground">
          doc #{activeSpan.document_id} — p.{activeSpan.page} (¶{activeSpan.span[0]}–{activeSpan.span[1]})
        </div>
        <div className="rounded-xl bg-muted/30 p-3 font-mono text-xs text-foreground border border-border/40">
          [doc {activeSpan.document_id} page {activeSpan.page} ¶{activeSpan.span[0]}–{activeSpan.span[1]}]
        </div>
      </div>
    );
  }

  return null;
}

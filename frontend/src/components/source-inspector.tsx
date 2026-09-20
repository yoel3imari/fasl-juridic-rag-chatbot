"use client";

import type { Citation, SpanRef } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { BookOpen, FileText, Scale, Copy, Check, ShieldCheck } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
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
      <div className="flex h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-border/80 p-8 text-center bg-muted/10">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground mb-3">
          <BookOpen className="h-6 w-6" />
        </div>
        <h4 className="text-sm font-bold text-foreground">
          {t.sourceInspector.placeholderTitle}
        </h4>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
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
      <Card className="border-border/80 shadow-sm animate-in fade-in duration-200">
        <CardHeader className="p-4 pb-3 border-b border-border/60">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge variant={isMatter ? "matter" : "authority"}>
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
              className="h-7 text-xs gap-1"
            >
              {copied ? (
                <>
                  <Check className="h-3 w-3 text-emerald-500" />
                  <span>{t.common.copied}</span>
                </>
              ) : (
                <>
                  <Copy className="h-3 w-3" />
                  <span>{t.sourceInspector.copyReference}</span>
                </>
              )}
            </Button>
          </div>

          <CardTitle className="text-base font-bold text-foreground mt-2 flex items-center gap-2">
            {isMatter ? (
              <>
                <FileText className="h-5 w-5 text-sky-500" />
                <span>
                  {t.sourceInspector.docNumber
                    .replace("{id}", String(activeCitation.document_id))
                    .replace("{docType}", activeCitation.doc_type)}
                </span>
              </>
            ) : (
              <>
                <Scale className="h-5 w-5 text-amber-500" />
                <span>
                  {t.sourceInspector.legalCode
                    .replace("{source}", activeCitation.source)
                    .replace("{article}", activeCitation.article_or_section)}
                </span>
              </>
            )}
          </CardTitle>
        </CardHeader>

        <CardContent className="p-4 space-y-3">
          {isMatter ? (
            <>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg bg-muted/40 p-2">
                  <span className="text-muted-foreground block text-[10px]">Page:</span>
                  <span className="font-bold text-foreground">p. {activeCitation.page}</span>
                </div>
                <div className="rounded-lg bg-muted/40 p-2">
                  <span className="text-muted-foreground block text-[10px]">Paragraph:</span>
                  <span className="font-bold text-foreground font-mono">
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
            </>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="rounded-lg bg-muted/40 p-2">
                  <span className="text-muted-foreground block text-[10px]">Version:</span>
                  <span className="font-bold text-foreground">{activeCitation.version}</span>
                </div>
                <div className="rounded-lg bg-muted/40 p-2">
                  <span className="text-muted-foreground block text-[10px]">Edition:</span>
                  <span className="font-bold text-foreground">{activeCitation.edition}</span>
                </div>
                <div className="rounded-lg bg-muted/40 p-2">
                  <span className="text-muted-foreground block text-[10px]">Language:</span>
                  <span className="font-bold text-foreground">{activeCitation.language}</span>
                </div>
              </div>

              <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-foreground leading-relaxed">
                <div className="text-[10px] font-bold text-amber-700 dark:text-amber-300 uppercase mb-1 flex items-center gap-1">
                  <ShieldCheck className="h-3 w-3" /> {activeCitation.source}
                </div>
                <div className="font-semibold text-foreground mb-1">
                  {activeCitation.article_or_section}
                </div>
                <div className="text-muted-foreground text-xs">
                  {t.sourceInspector.versionLabel.replace("{version}", activeCitation.version)} · {t.sourceInspector.editionLabel.replace("{edition}", activeCitation.edition)}
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    );
  }

  if (activeSpan) {
    return (
      <Card className="border-border/80 shadow-sm animate-in fade-in duration-200">
        <CardHeader className="p-4 pb-3 border-b border-border/60">
          <Badge variant="matter">{t.sourceInspector.matterBadge}</Badge>
          <CardTitle className="text-sm font-bold mt-2">
            doc #{activeSpan.document_id} — p.{activeSpan.page} (¶{activeSpan.span[0]}–{activeSpan.span[1]})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4">
          <div className="rounded-xl bg-muted/30 p-3 font-mono text-xs text-foreground">
            [doc {activeSpan.document_id} page {activeSpan.page} ¶{activeSpan.span[0]}–{activeSpan.span[1]}]
          </div>
        </CardContent>
      </Card>
    );
  }

  return null;
}

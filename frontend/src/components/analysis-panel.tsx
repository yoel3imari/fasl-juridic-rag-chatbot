"use client";

import { useState } from "react";
import { ApiError, runAnalysis, type AnalysisOut, type SpanRef } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  Sparkles,
  AlertTriangle,
  FileQuestion,
  Calendar,
  Users,
  FileText,
  AlertOctagon,
  Scale,
  Languages,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function AnalysisPanel({
  matterId,
  onSelectSpan,
}: {
  matterId: number | null;
  onSelectSpan?: (span: SpanRef) => void;
}) {
  const { t, language } = useI18n();
  const [data, setData] = useState<AnalysisOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summaryLang, setSummaryLang] = useState<"ar" | "fr">(language === "fr" ? "fr" : "ar");

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
            : t.analysis.failedAnalysis,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="analysis" className="space-y-4">
      {/* Run Action Header */}
      <div className="flex items-center justify-between rounded-xl border border-border/60 bg-background/40 p-3 shadow-2xs">
        <div>
          <h3 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
            <Scale className="h-3.5 w-3.5 text-primary" />
            <span>{t.analysis.headerTitle}</span>
          </h3>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {t.analysis.headerSubtitle}
          </p>
        </div>

        <Button
          type="button"
          size="sm"
          onClick={() => void run()}
          disabled={matterId === null || busy}
          className="h-8 gap-1.5 text-xs font-medium rounded-lg shadow-2xs"
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span>{busy ? t.analysis.runningButton : t.analysis.runButton}</span>
        </Button>
      </div>

      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium animate-in fade-in"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {data && data.content.status === "needs-documents" && (
        <div className="rounded-2xl border border-dashed border-amber-500/40 bg-amber-500/5 p-6 text-center">
          <FileQuestion className="h-7 w-7 text-amber-500 mx-auto mb-2" />
          <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">
            {t.analysis.needsDocsTitle}
          </p>
          <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
            {t.analysis.needsDocsSubtitle}
          </p>
        </div>
      )}

      {data && data.content.status === "complete" && (
        <div className="space-y-3 animate-in fade-in duration-200">
          {/* 1. Legal Risk Assessment */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                <AlertOctagon className="h-3.5 w-3.5 text-destructive/80" />
                <span>
                  {t.analysis.risksTitle.replace(
                    "{count}",
                    String(data.content.issues.length),
                  )}
                </span>
              </h4>
            </div>

            <div className="space-y-2">
              {data.content.issues.map((row, i) => (
                <div
                  key={i}
                  className="rounded-xl border border-border/60 bg-background/40 p-3 text-xs transition-all hover:bg-muted/30 shadow-2xs"
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <div className="font-semibold text-foreground text-xs">
                      {row.issue}
                    </div>
                    <Badge
                      variant={
                        row.risk === "High"
                          ? "risk-high"
                          : row.risk === "Medium"
                            ? "risk-medium"
                            : "risk-low"
                      }
                      className="text-[10px] font-medium py-0 px-2 shrink-0"
                    >
                      {row.risk === "High"
                        ? t.analysis.riskHigh
                        : row.risk === "Medium"
                          ? t.analysis.riskMedium
                          : t.analysis.riskLow}
                    </Badge>
                  </div>

                  <p className="text-muted-foreground leading-relaxed text-[11px]">
                    {row.finding}
                  </p>

                  {row.span_refs && row.span_refs.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5 pt-2 border-t border-border/40">
                      <span className="text-[10px] font-medium text-muted-foreground">
                        {t.analysis.groundedSource}
                      </span>
                      {row.span_refs.map((r, rIdx) => (
                        <button
                          key={rIdx}
                          type="button"
                          onClick={() => onSelectSpan?.(r)}
                          className="inline-flex items-center gap-1 rounded-md border border-sky-400/30 bg-sky-500/10 px-2 py-0.5 text-[10px] font-mono text-sky-700 dark:text-sky-300 hover:bg-sky-500/20 cursor-pointer transition-colors"
                        >
                          <FileText className="h-3 w-3" />
                          <span>
                            doc #{r.document_id} p.{r.page} ¶{r.span[0]}–{r.span[1]}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* 2. Key Dates Timeline & Obligations */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            {/* Dates */}
            {data.content.dates && data.content.dates.length > 0 && (
              <div className="rounded-xl border border-border/60 bg-background/40 p-3 space-y-2 shadow-2xs">
                <h4 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                  <Calendar className="h-3.5 w-3.5 text-primary" />
                  <span>{t.analysis.datesTitle}</span>
                </h4>
                <div className="space-y-1.5 max-h-44 overflow-y-auto">
                  {data.content.dates.map((d, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between rounded-lg bg-muted/30 p-2 text-xs border border-border/30"
                    >
                      <span className="text-muted-foreground text-[11px]">{d.label}</span>
                      <span className="font-semibold text-foreground font-mono text-[11px]">{d.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Parties & Obligations */}
            {data.content.obligations && data.content.obligations.length > 0 && (
              <div className="rounded-xl border border-border/60 bg-background/40 p-3 space-y-2 shadow-2xs">
                <h4 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                  <Users className="h-3.5 w-3.5 text-primary" />
                  <span>{t.analysis.obligationsTitle}</span>
                </h4>
                <div className="space-y-1.5 max-h-44 overflow-y-auto">
                  {data.content.obligations.map((ob, idx) => (
                    <div
                      key={idx}
                      className="rounded-lg bg-muted/30 p-2 text-xs space-y-0.5 border border-border/30"
                    >
                      <div className="font-semibold text-primary text-[11px]">{ob.who}</div>
                      <div className="text-muted-foreground text-[11px] leading-relaxed">{ob.what}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 3. Gaps / Lacunes */}
          {data.content.gaps && data.content.gaps.length > 0 && (
            <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3.5 space-y-1.5 shadow-2xs">
              <h4 className="text-xs font-semibold text-amber-900 dark:text-amber-200 flex items-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                <span>{t.analysis.gapsTitle}</span>
              </h4>
              <ul className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
                {data.content.gaps.map((gap, gIdx) => (
                  <li key={gIdx} className="flex items-start gap-2 text-[11px]">
                    <span className="text-amber-500 font-bold">•</span>
                    <span>{gap}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 4. Bilingual Synthesis Summaries */}
          <div className="rounded-xl border border-border/60 bg-background/40 p-3.5 space-y-2.5 shadow-2xs">
            <div className="flex items-center justify-between border-b border-border/50 pb-2">
              <h4 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                <Languages className="h-3.5 w-3.5 text-primary" />
                <span>{t.analysis.summaryTitle}</span>
              </h4>
              <div className="flex items-center gap-1 rounded-lg bg-muted/50 p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setSummaryLang("ar")}
                  className={`px-2 py-0.5 rounded-md text-[11px] font-medium cursor-pointer transition-colors ${
                    summaryLang === "ar"
                      ? "bg-card text-foreground font-semibold shadow-2xs"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t.analysis.summaryAr}
                </button>
                <button
                  type="button"
                  onClick={() => setSummaryLang("fr")}
                  className={`px-2 py-0.5 rounded-md text-[11px] font-medium cursor-pointer transition-colors ${
                    summaryLang === "fr"
                      ? "bg-card text-foreground font-semibold shadow-2xs"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t.analysis.summaryFr}
                </button>
              </div>
            </div>

            <div className="rounded-lg bg-muted/20 p-3 text-xs leading-relaxed whitespace-pre-line text-foreground">
              {summaryLang === "ar"
                ? data.content.summary_ar || t.analysis.noSummary
                : data.content.summary_fr || "Aucune synthèse disponible"}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

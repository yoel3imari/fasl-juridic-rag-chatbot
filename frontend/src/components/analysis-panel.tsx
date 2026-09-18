"use client";

import { useState } from "react";
import { ApiError, runAnalysis, type AnalysisOut, type SpanRef } from "@/lib/api";
import {
  Sparkles,
  AlertTriangle,
  FileQuestion,
  Calendar,
  Users,
  CheckCircle2,
  FileText,
  AlertOctagon,
  Scale,
  Languages,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function AnalysisPanel({
  matterId,
  onSelectSpan,
}: {
  matterId: number | null;
  onSelectSpan?: (span: SpanRef) => void;
}) {
  const [data, setData] = useState<AnalysisOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summaryLang, setSummaryLang] = useState<"ar" | "fr">("ar");

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
            : "فشل استخراج التحليل القانوني",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="analysis" className="space-y-4">
      {/* Run Action Header */}
      <div className="flex items-center justify-between rounded-xl border border-border/80 bg-muted/20 p-3.5">
        <div>
          <h3 className="text-sm font-bold text-foreground flex items-center gap-1.5">
            <Scale className="h-4 w-4 text-primary" />
            <span>التحليل القانوني الشامل وتقييم المخاطر</span>
          </h3>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            استخراج المخاطر القانونية، التزامات الأطراف، النواقص، وتضارب الوقائع.
          </p>
        </div>

        <Button
          type="button"
          onClick={() => void run()}
          disabled={matterId === null || busy}
          className="gap-1.5 text-xs font-semibold shadow-sm"
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span>{busy ? "جارٍ التحليل والتقييم…" : "تشغيل التحليل / Analyser"}</span>
        </Button>
      </div>

      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {data && data.content.status === "needs-documents" && (
        <div className="rounded-2xl border border-dashed border-amber-500/40 bg-amber-500/10 p-6 text-center">
          <FileQuestion className="h-8 w-8 text-amber-500 mx-auto mb-2" />
          <p className="text-sm font-bold text-amber-900 dark:text-amber-200">
            لا توجد وثائق كافية للتحليل
          </p>
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
            يرجى رفع عقد العمل، الإنذارات أو المراسلات في تبويب الوثائق أولاً.
          </p>
        </div>
      )}

      {data && data.content.status === "complete" && (
        <div className="space-y-4 animate-in fade-in duration-300">
          {/* 1. Legal Risk Assessment */}
          <Card className="border-border/80">
            <CardHeader className="p-4 pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-bold flex items-center gap-2 text-foreground">
                  <AlertOctagon className="h-4 w-4 text-destructive" />
                  <span>المخاطر والعيوب الشكلية والقانونية ({data.content.issues.length})</span>
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent className="p-4 pt-2 space-y-2">
              {data.content.issues.map((row, i) => (
                <div
                  key={i}
                  className="rounded-xl border border-border/60 bg-muted/30 p-3 text-xs transition-all hover:bg-muted/50"
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <div className="font-bold text-foreground text-sm">
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
                    >
                      {row.risk === "High"
                        ? "خطر مرتفع (High)"
                        : row.risk === "Medium"
                          ? "متوسط (Medium)"
                          : "منخفض (Low)"}
                    </Badge>
                  </div>

                  <p className="text-muted-foreground leading-relaxed">
                    {row.finding}
                  </p>

                  {row.span_refs && row.span_refs.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5 pt-2 border-t border-border/40">
                      <span className="text-[10px] font-semibold text-muted-foreground">
                        السند من الوثائق:
                      </span>
                      {row.span_refs.map((r, rIdx) => (
                        <button
                          key={rIdx}
                          type="button"
                          onClick={() => onSelectSpan?.(r)}
                          className="inline-flex items-center gap-1 rounded-md border border-sky-400/30 bg-sky-500/10 px-2 py-0.5 text-[10px] font-mono text-sky-700 dark:text-sky-300 hover:bg-sky-500/20 cursor-pointer"
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
            </CardContent>
          </Card>

          {/* 2. Key Dates Timeline & Obligations */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Dates */}
            {data.content.dates && data.content.dates.length > 0 && (
              <Card className="p-3.5 space-y-2">
                <h4 className="text-xs font-bold text-foreground flex items-center gap-1.5">
                  <Calendar className="h-3.5 w-3.5 text-primary" />
                  <span>التواريخ والآجال القانونية</span>
                </h4>
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {data.content.dates.map((d, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between rounded-lg bg-muted/40 p-2 text-xs"
                    >
                      <span className="text-muted-foreground">{d.label}</span>
                      <span className="font-bold text-foreground font-mono">{d.value}</span>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Parties & Obligations */}
            {data.content.obligations && data.content.obligations.length > 0 && (
              <Card className="p-3.5 space-y-2">
                <h4 className="text-xs font-bold text-foreground flex items-center gap-1.5">
                  <Users className="h-3.5 w-3.5 text-primary" />
                  <span>التزامات الأطراف</span>
                </h4>
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {data.content.obligations.map((ob, idx) => (
                    <div
                      key={idx}
                      className="rounded-lg bg-muted/40 p-2 text-xs space-y-0.5"
                    >
                      <div className="font-semibold text-primary">{ob.who}</div>
                      <div className="text-muted-foreground">{ob.what}</div>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>

          {/* 3. Gaps / Lacunes */}
          {data.content.gaps && data.content.gaps.length > 0 && (
            <Card className="border-amber-500/30 bg-amber-500/5 p-4 space-y-2">
              <h4 className="text-xs font-bold text-amber-900 dark:text-amber-200 flex items-center gap-1.5">
                <AlertTriangle className="h-4 w-4 text-amber-500" />
                <span>النواقص والوثائق الغائبة (Lacunes)</span>
              </h4>
              <ul className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
                {data.content.gaps.map((gap, gIdx) => (
                  <li key={gIdx} className="flex items-start gap-2">
                    <span className="text-amber-500 font-bold">•</span>
                    <span>{gap}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* 4. Bilingual Synthesis Summaries */}
          <Card className="p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-border/60 pb-2">
              <h4 className="text-xs font-bold text-foreground flex items-center gap-1.5">
                <Languages className="h-4 w-4 text-primary" />
                <span>الملخص التنفيذي للملف / Synthèse</span>
              </h4>
              <div className="flex items-center gap-1 rounded-lg bg-muted p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setSummaryLang("ar")}
                  className={`px-2 py-0.5 rounded-md font-medium cursor-pointer transition-colors ${
                    summaryLang === "ar"
                      ? "bg-card text-foreground font-bold shadow-xs"
                      : "text-muted-foreground"
                  }`}
                >
                  العربية
                </button>
                <button
                  type="button"
                  onClick={() => setSummaryLang("fr")}
                  className={`px-2 py-0.5 rounded-md font-medium cursor-pointer transition-colors ${
                    summaryLang === "fr"
                      ? "bg-card text-foreground font-bold shadow-xs"
                      : "text-muted-foreground"
                  }`}
                >
                  Français
                </button>
              </div>
            </div>

            <div className="rounded-xl bg-muted/30 p-3 text-xs leading-relaxed whitespace-pre-line text-foreground">
              {summaryLang === "ar"
                ? data.content.summary_ar || "لا يوجد ملخص متوفر"
                : data.content.summary_fr || "Aucune synthèse disponible"}
            </div>
          </Card>
        </div>
      )}
    </section>
  );
}

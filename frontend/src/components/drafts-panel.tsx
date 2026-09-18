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
import {
  FileText,
  PenTool,
  CheckCircle,
  ShieldAlert,
  UserCheck,
  Sparkles,
  Copy,
  Check,
  Mail,
  Scale,
  BookOpen,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const DRAFT_TYPE_OPTIONS = [
  {
    value: "demand_letter",
    label: "إنذار مباشر / Mise en demeure (Demand Letter)",
  },
  {
    value: "opinion",
    label: "رأي واستشارة قانونية / Avis juridique (Legal Opinion)",
  },
  {
    value: "client_email",
    label: "رسالة تفسيرية للموكل / Courriel client",
  },
  {
    value: "memo",
    label: "مذكرة دفاع أو بيان وقائع / Mémoire interne",
  },
];

export function DraftsPanel({ matterId }: { matterId: number | null }) {
  const [draftType, setDraftType] = useState<string>("demand_letter");
  const [draft, setDraft] = useState<DraftOut | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const guard = () => {
    if (matterId === null) {
      setError("اختر أو أنشئ ملف قضية أولاً للبدء في الصياغة");
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
            : "فشل إنشاء أو مراجعة المسودة",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = () => {
    if (!draft?.content) return;
    navigator.clipboard.writeText(draft.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section aria-label="drafts" className="space-y-4">
      {/* Studio Controls Header */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 rounded-xl border border-border/80 bg-muted/20 p-3.5">
        <div className="flex-1">
          <label className="text-xs font-bold text-foreground block mb-1.5">
            نوع المحرر أو الوثيقة القانونية المراد صياغتها:
          </label>
          <Select value={draftType} onValueChange={setDraftType}>
            <SelectTrigger className="w-full bg-card">
              <SelectValue placeholder="اختر نوع المسودة" />
            </SelectTrigger>
            <SelectContent>
              {DRAFT_TYPE_OPTIONS.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="sm:self-end">
          <Button
            type="button"
            disabled={busy || matterId === null}
            onClick={() =>
              void wrap(() =>
                createDraft(
                  matterId as number,
                  draftType as "opinion" | "client_email" | "demand_letter" | "memo",
                ),
              )
            }
            className="w-full sm:w-auto gap-1.5 text-xs font-semibold shadow-sm"
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>{busy ? "جارٍ التوليد والصياغة…" : "توليد المسودة / Générer"}</span>
          </Button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium"
        >
          {error}
        </div>
      )}

      {/* Generated Draft Output Card */}
      {draft && (
        <Card className="border-border/80 shadow-md animate-in fade-in duration-300">
          <CardHeader className="p-4 pb-3 border-b border-border/60">
            {/* Status & Review Workflow Bar */}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    draft.review_state === "lawyer_reviewed"
                      ? "risk-low"
                      : draft.review_state === "acknowledged"
                        ? "authority"
                        : "secondary"
                  }
                  className="text-xs py-1"
                >
                  {draft.review_state === "lawyer_reviewed" ? (
                    <span className="flex items-center gap-1">
                      <CheckCircle className="h-3.5 w-3.5" />
                      مراجعة ومؤشرة من المحامي (Lawyer Reviewed)
                    </span>
                  ) : draft.review_state === "acknowledged" ? (
                    <span className="flex items-center gap-1">
                      <Check className="h-3.5 w-3.5" />
                      تم الإقرار بالاستلام (Acknowledged)
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <PenTool className="h-3.5 w-3.5" />
                      مسودة أولية قيد الإعداد (Draft)
                    </span>
                  )}
                </Badge>
              </div>

              {/* Copy & Actions */}
              <Button
                variant="outline"
                size="sm"
                onClick={handleCopy}
                className="h-8 gap-1 text-xs"
              >
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-500" />
                    <span>تم النسخ</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" />
                    <span>نسخ النص</span>
                  </>
                )}
              </Button>
            </div>

            {/* Moroccan Law Provisional Warning Banner */}
            <div
              role="status"
              className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-900 dark:text-amber-200 flex items-start gap-2"
            >
              <ShieldAlert className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
              <div>
                <span className="font-bold">تنبيه قانوني: </span>
                <span>{draft.provisional_banner} — {draft.status_label}</span>
              </div>
            </div>
          </CardHeader>

          <CardContent className="p-4 space-y-4">
            {/* Draft Text Content */}
            <div className="rounded-xl border border-border/60 bg-muted/20 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground">
              {draft.content}
            </div>

            {/* Citations in Draft */}
            {draft.citations && draft.citations.length > 0 && (
              <div className="space-y-1.5">
                <span className="text-xs font-bold text-muted-foreground flex items-center gap-1">
                  <BookOpen className="h-3.5 w-3.5 text-primary" />
                  السند القانوني والمستندات المعتمدة في الصياغة:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {draft.citations.map((c, i) => (
                    <CitationDomainBadge
                      key={`${c.domain}-${i}`}
                      citation={c}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Workflow Review Actions */}
            <div className="rounded-xl border border-border/80 bg-muted/30 p-3.5">
              <h4 className="text-xs font-bold text-foreground mb-2 flex items-center gap-1.5">
                <UserCheck className="h-4 w-4 text-primary" />
                <span>إجراءات المصادقة والمراجعة المهنية</span>
              </h4>

              {draft.review_state === "draft" && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void wrap(() => acknowledgeDraft(draft.draft_id))}
                    className="gap-1.5 text-xs font-semibold"
                  >
                    <Check className="h-3.5 w-3.5" />
                    <span>إقرار باستلام المسودة / Accuser réception</span>
                  </Button>
                </div>
              )}

              {draft.review_state !== "lawyer_reviewed" && (
                <div className="mt-3 flex flex-wrap items-center gap-2 pt-3 border-t border-border/60">
                  <div className="flex-1 min-w-[200px]">
                    <Input
                      aria-label="reviewer"
                      value={reviewer}
                      onChange={(e) => setReviewer(e.target.value)}
                      placeholder="اسم المحامي المراجع (مثال: الأستاذ بناني)..."
                      className="h-8 text-xs"
                    />
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="emerald"
                    disabled={busy || !reviewer.trim()}
                    onClick={() =>
                      void wrap(() =>
                        lawyerReviewDraft(draft.draft_id, reviewer.trim()),
                      )
                    }
                    className="h-8 text-xs font-semibold"
                  >
                    <UserCheck className="h-3.5 w-3.5" />
                    <span>تأشير ومراجعة المحامي / Révision avocat</span>
                  </Button>
                </div>
              )}

              {draft.review_state === "lawyer_reviewed" && (
                <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400 font-semibold">
                  <CheckCircle className="h-4 w-4" />
                  <span>
                    تمت المراجعة والتأشير بواسطة: {draft.reviewer} بتاريخ {draft.reviewed_at}
                  </span>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </section>
  );
}

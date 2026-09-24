"use client";

import { useState } from "react";
import {
  acknowledgeDraft,
  ApiError,
  createDraft,
  lawyerReviewDraft,
  type DraftOut,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { CitationDomainBadge } from "./citation-domain-badge";
import {
  CheckCircle,
  ShieldAlert,
  UserCheck,
  Sparkles,
  Copy,
  Check,
  BookOpen,
  PenTool,
} from "lucide-react";
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

export function DraftsPanel({ matterId }: { matterId: number | null }) {
  const { t } = useI18n();
  const [draftType, setDraftType] = useState<string>("demand_letter");
  const [draft, setDraft] = useState<DraftOut | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const draftOptions = [
    { value: "demand_letter", label: t.drafts.demandLetter },
    { value: "opinion", label: t.drafts.opinion },
    { value: "client_email", label: t.drafts.clientEmail },
    { value: "memo", label: t.drafts.memo },
  ];

  const guard = () => {
    if (matterId === null) {
      setError(t.drafts.errorSelectMatter);
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
            : t.drafts.errorFailed,
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
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 rounded-xl border border-border/60 bg-background/40 p-3 shadow-2xs">
        <div className="flex-1">
          <label className="text-[11px] font-semibold text-muted-foreground block mb-1">
            {t.drafts.typeSelectLabel}
          </label>
          <Select value={draftType} onValueChange={setDraftType}>
            <SelectTrigger className="w-full h-8 rounded-lg border-border/60 bg-background/60 text-xs font-medium shadow-2xs">
              <SelectValue placeholder={t.drafts.draftTypePlaceholder} />
            </SelectTrigger>
            <SelectContent className="rounded-xl border-border/70 shadow-lg">
              {draftOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="sm:self-end">
          <Button
            type="button"
            size="sm"
            disabled={busy || matterId === null}
            onClick={() =>
              void wrap(() =>
                createDraft(
                  matterId as number,
                  draftType as "opinion" | "client_email" | "demand_letter" | "memo",
                ),
              )
            }
            className="w-full sm:w-auto h-8 gap-1.5 text-xs font-medium rounded-lg shadow-2xs"
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>{busy ? t.drafts.generatingButton : t.drafts.generateButton}</span>
          </Button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium animate-in fade-in"
        >
          {error}
        </div>
      )}

      {/* Generated Draft Output Card */}
      {draft && (
        <div className="space-y-3 animate-in fade-in duration-200">
          <div className="rounded-2xl border border-border/70 bg-card shadow-2xs overflow-hidden">
            {/* Document Action & Status Header */}
            <div className="p-3 sm:p-4 border-b border-border/50 bg-muted/15 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    draft.review_state === "lawyer_reviewed"
                      ? "risk-low"
                      : draft.review_state === "acknowledged"
                        ? "authority"
                        : "secondary"
                  }
                  className="text-[11px] font-medium py-0.5 px-2.5"
                >
                  {draft.review_state === "lawyer_reviewed" ? (
                    <span className="flex items-center gap-1">
                      <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />
                      {t.drafts.lawyerReviewedBadge}
                    </span>
                  ) : draft.review_state === "acknowledged" ? (
                    <span className="flex items-center gap-1">
                      <Check className="h-3.5 w-3.5 text-sky-500" />
                      {t.drafts.acknowledgedBadge}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <PenTool className="h-3.5 w-3.5 text-muted-foreground" />
                      {t.drafts.draftBadge}
                    </span>
                  )}
                </Badge>
              </div>

              {/* Copy Action */}
              <Button
                variant="outline"
                size="sm"
                onClick={handleCopy}
                className="h-7 rounded-lg border-border/60 bg-background/50 hover:bg-muted/50 gap-1 text-xs shadow-2xs"
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3 text-emerald-500" />
                    <span className="text-emerald-500 font-medium">{t.common.copied}</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" />
                    <span>{t.drafts.copyText}</span>
                  </>
                )}
              </Button>
            </div>

            {/* Moroccan Law Provisional Warning Banner */}
            {draft.provisional_banner && (
              <div
                role="status"
                className="mx-3 sm:mx-4 mt-3 rounded-xl border border-amber-500/25 bg-amber-500/5 p-2.5 text-xs text-amber-900 dark:text-amber-200 flex items-start gap-2"
              >
                <ShieldAlert className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
                <div className="text-[11px] leading-relaxed">
                  <span className="font-semibold">تنبيه قانوني: </span>
                  <span>{draft.provisional_banner} — {draft.status_label}</span>
                </div>
              </div>
            )}

            {/* Document Canvas Body */}
            <div className="p-4 sm:p-6 space-y-4">
              <div className="rounded-xl border border-border/50 bg-muted/10 p-4 sm:p-5 font-serif text-sm leading-relaxed whitespace-pre-wrap text-foreground select-text shadow-inner">
                {draft.content}
              </div>

              {/* Citations in Draft */}
              {draft.citations && draft.citations.length > 0 && (
                <div className="space-y-1.5 pt-1">
                  <span className="text-[11px] font-semibold text-muted-foreground flex items-center gap-1">
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
            </div>

            {/* Workflow Review Actions Footer */}
            <div className="p-3 sm:p-4 border-t border-border/50 bg-muted/15">
              <h4 className="text-xs font-semibold text-foreground mb-2 flex items-center gap-1.5">
                <UserCheck className="h-3.5 w-3.5 text-primary" />
                <span>{t.drafts.acknowledgeTitle}</span>
              </h4>

              {draft.review_state === "draft" && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void wrap(() => acknowledgeDraft(draft.draft_id))}
                    className="h-8 gap-1.5 text-xs font-medium rounded-lg shadow-2xs"
                  >
                    <Check className="h-3.5 w-3.5" />
                    <span>{t.drafts.acknowledgeButton}</span>
                  </Button>
                </div>
              )}

              {draft.review_state !== "lawyer_reviewed" && (
                <div className="mt-2.5 flex flex-wrap items-center gap-2 pt-2.5 border-t border-border/40">
                  <div className="flex-1 min-w-[180px]">
                    <Input
                      aria-label="reviewer"
                      value={reviewer}
                      onChange={(e) => setReviewer(e.target.value)}
                      placeholder={t.drafts.reviewerPlaceholder}
                      className="h-8 text-xs rounded-lg border-border/60 bg-background/60 shadow-2xs"
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
                    className="h-8 text-xs font-medium rounded-lg shadow-2xs"
                  >
                    <UserCheck className="h-3.5 w-3.5" />
                    <span>{t.drafts.reviewButton}</span>
                  </Button>
                </div>
              )}

              {draft.review_state === "lawyer_reviewed" && (
                <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
                  <CheckCircle className="h-4 w-4 shrink-0" />
                  <span>
                    تمت المراجعة والتأشير بواسطة: {draft.reviewer} بتاريخ {draft.reviewed_at}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

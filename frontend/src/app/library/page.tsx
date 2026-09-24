"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  libraryCoverage,
  type CoverageEntry,
  type CoverageResponse,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/language-switcher";
import {
  Scale,
  ArrowRight,
  ArrowLeft,
  Search,
  BookOpen,
  AlertTriangle,
  Calendar,
  Layers,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { UploadLibraryModal } from "@/components/upload-library-modal";
import { Plus, Upload } from "lucide-react";

export default function LibraryPage() {
  const { t, isRTL } = useI18n();
  const [entries, setEntries] = useState<CoverageEntry[]>([]);
  const [gaps, setGaps] = useState<string[]>([]);
  const [libraryVersion, setLibraryVersion] = useState<string | null>(null);
  const [summary, setSummary] = useState<CoverageResponse["summary"]>(undefined);
  const [titlesTruncated, setTitlesTruncated] = useState<number>(0);
  const [gapsTruncated, setGapsTruncated] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [uploadModalOpen, setUploadModalOpen] = useState(false);

  const fetchCoverage = () => {
    libraryCoverage()
      .then((cov) => {
        setEntries(cov.titles ?? []);
        setGaps(cov.gaps ?? []);
        setLibraryVersion(cov.library_version ?? null);
        setSummary(cov.summary);
        setTitlesTruncated(cov.titles_truncated ?? 0);
        setGapsTruncated(cov.gaps_truncated ?? 0);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? `${e.status}: ${e.message}` : t.library.errorFailed),
      );
  };

  useEffect(() => {
    fetchCoverage();
  }, [t.library.errorFailed]);

  const filteredEntries = entries.filter(
    (e) =>
      e.source.toLowerCase().includes(search.toLowerCase()) ||
      e.version.toLowerCase().includes(search.toLowerCase()) ||
      e.edition.toLowerCase().includes(search.toLowerCase()) ||
      e.coverage_note?.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Top Bar */}
      <header className="border-b border-border/60 bg-card/60 backdrop-blur-md px-6 py-2.5 sticky top-0 z-40">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-background/50 px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted/50 transition-colors shadow-2xs"
            >
              {isRTL ? <ArrowRight className="h-3.5 w-3.5" /> : <ArrowLeft className="h-3.5 w-3.5" />}
              <span>{t.library.backToPlatform}</span>
            </Link>

            <div className="h-3.5 w-px bg-border/60" />

            <div className="flex items-center gap-2">
              <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-2xs">
                <Scale className="h-3.5 w-3.5" />
              </div>
              <h1 className="text-sm font-bold text-foreground tracking-tight" role="heading">
                {t.library.headerTitle}
              </h1>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {libraryVersion && (
              <Badge variant="outline" className="text-[10px] font-mono border-border/60">
                {t.library.libraryVersion.replace("{version}", libraryVersion)}
              </Badge>
            )}
            <LanguageSwitcher />
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="mx-auto max-w-6xl p-6 space-y-6">
        {/* Banner & Search */}
        <div className="rounded-2xl border border-border/70 bg-gradient-to-b from-primary/5 via-card to-card p-6 sm:p-8 shadow-2xs">
          <div className="max-w-2xl space-y-2">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-semibold text-primary">
              <ShieldCheck className="h-3 w-3" />
              <span>{t.library.bannerBadge}</span>
            </div>
            <h2 className="text-xl font-bold tracking-tight text-foreground sm:text-2xl">
              {t.library.bannerTitle}
            </h2>
            <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed">
              {t.library.bannerDesc}
            </p>
          </div>

          <div className="mt-5 max-w-xl">
            <Input
              icon={<Search className="h-4 w-4" />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t.library.searchPlaceholder}
              className="h-9 text-xs bg-background/80 rounded-xl border-border/70 shadow-2xs"
            />
          </div>
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

        {/* Summary Header */}
        {summary && (
          <section aria-label="Coverage summary" className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-2xl border border-border/70 bg-card p-4 shadow-2xs space-y-1">
              <div className="flex items-center justify-between text-muted-foreground">
                <span className="text-[11px] font-medium">{t.library.totalFiles}</span>
                <BookOpen className="h-3.5 w-3.5 text-primary" />
              </div>
              <div className="text-xl font-bold tracking-tight text-foreground">
                {summary.totals.files}
              </div>
              <p className="text-[10px] text-muted-foreground">
                {summary.totals.indexed} {t.library.indexedFiles.toLowerCase()}
              </p>
            </div>

            <div className="rounded-2xl border border-border/70 bg-card p-4 shadow-2xs space-y-1">
              <div className="flex items-center justify-between text-muted-foreground">
                <span className="text-[11px] font-medium">{t.library.totalChunks}</span>
                <Layers className="h-3.5 w-3.5 text-primary" />
              </div>
              <div className="text-xl font-bold tracking-tight text-foreground">
                {summary.totals.chunks_indexed.toLocaleString()}
              </div>
              <p className="text-[10px] text-muted-foreground">
                {summary.totals.chunks > summary.totals.chunks_indexed
                  ? `${summary.totals.chunks.toLocaleString()} extraits`
                  : t.library.indexedFiles}
              </p>
            </div>

            <div className="rounded-2xl border border-border/70 bg-card p-4 shadow-2xs space-y-1">
              <div className="flex items-center justify-between text-muted-foreground">
                <span className="text-[11px] font-medium">{t.library.categoriesCount}</span>
                <Scale className="h-3.5 w-3.5 text-primary" />
              </div>
              <div className="text-xl font-bold tracking-tight text-foreground">
                {Object.keys(summary.by_category).length}
              </div>
              <p className="text-[10px] text-muted-foreground font-mono">
                {Object.keys(summary.by_edition).join(" · ") || "ar-general"}
              </p>
            </div>

            <div className="rounded-2xl border border-border/70 bg-card p-4 shadow-2xs space-y-1">
              <div className="flex items-center justify-between text-muted-foreground">
                <span className="text-[11px] font-medium">{t.library.gapsTitle.split("/")[0].trim()}</span>
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
              </div>
              <div className="text-xl font-bold tracking-tight text-foreground">
                {gaps.length + gapsTruncated}
              </div>
              <p className="text-[10px] text-muted-foreground">
                {gapsTruncated > 0 ? `+${gapsTruncated} omission` : "Lacunes"}
              </p>
            </div>
          </section>
        )}

        {/* Legal Codes Grid */}
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                <BookOpen className="h-3.5 w-3.5 text-primary" />
                <span>{t.library.availableCodes.replace("{count}", String(filteredEntries.length))}</span>
              </h3>
              {titlesTruncated > 0 && (
                <Badge variant="outline" className="text-[10px] font-mono text-muted-foreground border-border/60">
                  {t.library.showingLimited
                    .replace("{count}", String(filteredEntries.length))
                    .replace("{total}", String(summary?.totals?.files ?? (entries.length + titlesTruncated)))}
                </Badge>
              )}
            </div>

            <Button
              type="button"
              size="sm"
              onClick={() => setUploadModalOpen(true)}
              className="h-8 rounded-lg gap-1.5 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 shadow-2xs transition-all cursor-pointer"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>{t.library.uploadButton}</span>
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
            {filteredEntries.map((e, i) => (
              <div
                key={i}
                className="flex flex-col justify-between rounded-2xl border border-border/70 bg-card p-5 hover:border-primary/50 transition-all shadow-2xs hover:shadow-xs space-y-3"
              >
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
                      <Scale className="h-4 w-4" />
                    </div>
                    <Badge variant="authority" className="text-[10px] font-medium">
                      {e.edition}
                    </Badge>
                  </div>

                  <h4 className="text-sm font-semibold text-foreground mt-3">
                    {e.source}
                  </h4>
                  <p className="text-[11px] text-muted-foreground font-mono mt-0.5">
                    {t.sourceInspector.versionLabel.replace("{version}", e.version)}
                  </p>
                </div>

                <div className="space-y-2.5 pt-2">
                  {e.coverage_note && (
                    <p className="text-[11px] text-muted-foreground leading-relaxed bg-muted/20 p-2 rounded-lg border border-border/30">
                      {e.coverage_note}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/50 text-[11px] text-muted-foreground">
                    {e.chunks !== undefined && (
                      <span className="flex items-center gap-1 font-medium text-primary">
                        <Layers className="h-3 w-3" />
                        {t.library.chunksCount.replace("{count}", String(e.chunks))}
                      </span>
                    )}
                    {e.pub_date && (
                      <span className="flex items-center gap-1">
                        <Calendar className="h-3 w-3" />
                        {t.library.publishedDate.replace("{date}", e.pub_date)}
                      </span>
                    )}
                    {e.language && (
                      <span className="rounded bg-muted/60 px-1.5 py-0.2 text-[10px] font-semibold">
                        {e.language}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}

            {filteredEntries.length === 0 && !error && (
              <div className="col-span-full py-12 text-center text-xs text-muted-foreground">
                {t.library.emptyLibrary}
              </div>
            )}
          </div>
        </div>

        {gaps.length > 0 && (
          <section className="rounded-2xl border border-amber-500/25 bg-amber-500/5 p-5 space-y-2.5 shadow-2xs">
            <h3 className="text-sm font-semibold text-amber-900 dark:text-amber-200 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              <span>{t.library.gapsTitle}</span>
            </h3>
            <p className="text-[11px] text-amber-700 dark:text-amber-300">
              {t.library.gapsDesc}
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
              {gaps.map((g, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-xl bg-card/90 p-2.5 text-xs font-medium text-foreground border border-border/50 shadow-2xs"
                >
                  <span className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-amber-600 font-bold text-[10px]">
                    {i + 1}
                  </span>
                  <span className="text-[11px]">{g}</span>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>

      {/* Upload Authority Modal */}
      <UploadLibraryModal
        open={uploadModalOpen}
        onOpenChange={setUploadModalOpen}
        onDocumentUploaded={() => fetchCoverage()}
      />
    </div>
  );
}

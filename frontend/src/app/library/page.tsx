"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  libraryCoverage,
  type CoverageEntry,
} from "@/lib/api";
import {
  Scale,
  ArrowRight,
  Search,
  BookOpen,
  AlertTriangle,
  FileCheck2,
  Calendar,
  Layers,
  Sparkles,
  ShieldCheck,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function LibraryPage() {
  const [entries, setEntries] = useState<CoverageEntry[]>([]);
  const [gaps, setGaps] = useState<string[]>([]);
  const [libraryVersion, setLibraryVersion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    libraryCoverage()
      .then((cov) => {
        setEntries(cov.titles ?? []);
        setGaps(cov.gaps ?? []);
        setLibraryVersion(cov.library_version ?? null);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? `${e.status}: ${e.message}` : "فشل تحميل نصوص المكتبة القانونية"),
      );
  }, []);

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
      <header className="border-b border-border/80 bg-card/80 backdrop-blur-md px-6 py-3.5 sticky top-0 z-40">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="flex items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted transition-colors"
            >
              <ArrowRight className="h-4 w-4" />
              <span>العودة للمنصة / Retour</span>
            </Link>

            <div className="h-4 w-px bg-border/80" />

            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
                <Scale className="h-4 w-4" />
              </div>
              <h1 className="text-base font-bold text-foreground" role="heading">
                المكتبة القانونية المغربية / Bibliothèque juridique
              </h1>
            </div>
          </div>

          {libraryVersion && (
            <Badge variant="outline" className="text-[11px] font-mono">
              إصدار المكتبة: v{libraryVersion}
            </Badge>
          )}
        </div>
      </header>

      {/* Main Content */}
      <main className="mx-auto max-w-6xl p-6 space-y-6">
        {/* Banner & Search */}
        <div className="rounded-2xl border border-border/80 bg-gradient-to-b from-primary/5 via-card to-card p-6 sm:p-8 shadow-xs">
          <div className="max-w-2xl space-y-2">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1 text-xs font-bold text-primary">
              <ShieldCheck className="h-3.5 w-3.5" />
              <span>نصوص رسمية مؤصلة ومفهرسة بالنوازل</span>
            </div>
            <h2 className="text-2xl font-extrabold tracking-tight text-foreground sm:text-3xl">
              النصوص والمدونات القانونية المعتمدة
            </h2>
            <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed">
              تصفح نصوص القانون المغربي المفهرسة والمقسمة بدقة فقرة بفقرة ومادة بمادة، لتوفير استشهادات قطعية في التحليلات والمحادثات.
            </p>
          </div>

          <div className="mt-6 max-w-xl">
            <Input
              icon={<Search className="h-4 w-4" />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="ابحث في أسماء المدونات، المواد، أو الطبعات (مدونة الشغل، ق.ل.ع…)"
              className="h-11 text-sm bg-card shadow-sm"
            />
          </div>
        </div>

        {error && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive font-medium"
          >
            <AlertTriangle className="h-5 w-5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Legal Codes Grid */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <BookOpen className="h-4 w-4 text-primary" />
              <span>المدونات والنصوص المتاحة ({filteredEntries.length})</span>
            </h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredEntries.map((e, i) => (
              <Card
                key={i}
                className="flex flex-col justify-between border-border/80 hover:border-primary/50 transition-all shadow-xs hover:shadow-md"
              >
                <CardHeader className="p-5 pb-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
                      <Scale className="h-5 w-5" />
                    </div>
                    <Badge variant="authority" className="text-[10px]">
                      {e.edition}
                    </Badge>
                  </div>

                  <CardTitle className="text-base font-bold text-foreground mt-3">
                    {e.source}
                  </CardTitle>
                  <p className="text-xs text-muted-foreground font-mono">
                    الإصدار: {e.version}
                  </p>
                </CardHeader>

                <CardContent className="p-5 pt-0 space-y-3">
                  {e.coverage_note && (
                    <p className="text-xs text-muted-foreground leading-relaxed bg-muted/30 p-2.5 rounded-lg border border-border/40">
                      {e.coverage_note}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/60 text-[11px] text-muted-foreground">
                    {e.chunks !== undefined && (
                      <span className="flex items-center gap-1 font-semibold text-primary">
                        <Layers className="h-3 w-3" />
                        {e.chunks} فقرة مفهرسة
                      </span>
                    )}
                    {e.pub_date && (
                      <span className="flex items-center gap-1">
                        <Calendar className="h-3 w-3" />
                        نشر {e.pub_date}
                      </span>
                    )}
                    {e.language && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold">
                        {e.language}
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}

            {filteredEntries.length === 0 && !error && (
              <div className="col-span-full py-12 text-center text-sm text-muted-foreground">
                المكتبة فارغة أو لا توجد نتائج مطابقة لبحثك — Bibliothèque vide pour le moment
              </div>
            )}
          </div>
        </div>

        {/* Lacunes / Gaps Section */}
        {gaps.length > 0 && (
          <section className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-6 space-y-3">
            <h3 className="text-base font-bold text-amber-900 dark:text-amber-200 flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              <span>النواقص التشريعية قيد الإدراج / Lacunes</span>
            </h3>
            <p className="text-xs text-amber-700 dark:text-amber-300">
              النصوص والملاحق القانونية التالية يجري تدقيقها وفهرستها حالياً لضمها للمكتبة:
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2">
              {gaps.map((g, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-xl bg-card/80 p-3 text-xs font-medium text-foreground border border-border/60"
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-amber-600 font-bold text-[10px]">
                    {i + 1}
                  </span>
                  <span>{g}</span>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import Link from "next/link";
import {
  FolderOpen,
  Plus,
  Search,
  BookOpen,
  Sun,
  Moon,
  Scale,
  MapPin,
  Briefcase,
} from "lucide-react";
import { listMatters, type Matter } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/language-switcher";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function MatterBar({
  matterId,
  onSelect,
  onOpenNewMatterModal,
  onOpenCommandPalette,
}: {
  matterId: number | null;
  onSelect: (id: number | null) => void;
  onOpenNewMatterModal: () => void;
  onOpenCommandPalette: () => void;
}) {
  const [matters, setMatters] = useState<Matter[]>([]);
  const { theme, setTheme } = useTheme();
  const { t } = useI18n();

  const refresh = async () => {
    try {
      const list = await listMatters();
      setMatters(list);
      // If no matter selected yet and we have matters, default to first
      if (matterId === null && list.length > 0) {
        onSelect(list[0].id);
      }
    } catch {
      // Backend down
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const activeMatter = matters.find((m) => m.id === matterId);

  return (
    <header className="flex h-13 items-center justify-between gap-3 border-b border-border/60 bg-card/60 backdrop-blur-md px-4 sticky top-0 z-40 shrink-0">
      {/* Brand & Active Matter Breadcrumb */}
      <div className="flex items-center gap-3 min-w-0">
        <Link
          href="/"
          className="flex items-center gap-2 font-bold text-foreground hover:opacity-90 transition-opacity shrink-0"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-2xs">
            <Scale className="h-3.5 w-3.5" />
          </div>
          <span className="text-sm tracking-tight font-extrabold text-foreground">
            {t.common.appName}
          </span>
        </Link>

        <span className="text-muted-foreground/40 font-light select-none">/</span>

        {/* Matter Selector */}
        <div className="w-56 sm:w-64">
          <Select
            value={matterId ? String(matterId) : undefined}
            onValueChange={(val) => onSelect(val ? Number(val) : null)}
          >
            <SelectTrigger className="h-8 rounded-lg border-border/60 bg-background/60 text-xs font-medium shadow-2xs hover:bg-muted/40 transition-colors">
              <SelectValue placeholder={t.nav.selectMatterPlaceholder} />
            </SelectTrigger>
            <SelectContent className="rounded-xl border-border/70 shadow-lg">
              {matters.map((m) => (
                <SelectItem key={m.id} value={String(m.id)} className="text-xs">
                  <div className="flex items-center gap-2">
                    <FolderOpen className="h-3.5 w-3.5 text-sky-500 shrink-0" />
                    <span className="truncate font-medium">#{m.id} {m.title}</span>
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* New Matter Button */}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onOpenNewMatterModal}
          className="h-8 rounded-lg border-border/60 bg-background/60 hover:bg-muted/50 gap-1 text-xs font-medium shadow-2xs shrink-0"
        >
          <Plus className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{t.nav.newMatter}</span>
        </Button>

        {/* Matter Details Tags */}
        {activeMatter && (
          <div className="hidden xl:flex items-center gap-1.5 text-xs text-muted-foreground/80">
            <span className="rounded-md bg-muted/50 px-2 py-0.5 text-[11px] font-medium flex items-center gap-1 border border-border/40">
              <MapPin className="h-3 w-3 text-muted-foreground" />
              {activeMatter.jurisdiction}
            </span>
            <span className="rounded-md bg-muted/50 px-2 py-0.5 text-[11px] font-medium flex items-center gap-1 border border-border/40">
              <Briefcase className="h-3 w-3 text-muted-foreground" />
              {activeMatter.matter_type}
            </span>
          </div>
        )}
      </div>

      {/* Global Actions: Spotlight Trigger, Library, Language Switcher, Dark Mode */}
      <div className="flex items-center gap-1.5 shrink-0">
        {/* Spotlight Command Trigger */}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onOpenCommandPalette}
          className="h-8 rounded-lg border-border/60 bg-background/50 hover:bg-muted/50 gap-2 text-xs text-muted-foreground hover:text-foreground shadow-2xs"
        >
          <Search className="h-3.5 w-3.5 text-primary/80" />
          <span className="hidden md:inline">{t.nav.searchPlaceholder}</span>
          <kbd className="hidden sm:inline-flex items-center gap-0.5 rounded border border-border/70 bg-card px-1.5 py-0.2 text-[10px] font-mono text-muted-foreground">
            {t.nav.searchKbd}
          </kbd>
        </Button>

        {/* Library Link */}
        <Link href="/library">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 rounded-lg gap-1.5 text-xs font-medium text-foreground hover:bg-muted/50"
          >
            <BookOpen className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
            <span className="hidden lg:inline">{t.nav.legalLibrary}</span>
          </Button>
        </Link>

        {/* Language Switcher Dropdown */}
        <LanguageSwitcher />

        {/* Theme Toggle */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="h-8 w-8 rounded-lg text-foreground hover:bg-muted/50"
          aria-label={t.nav.toggleTheme}
        >
          {theme === "dark" ? (
            <Sun className="h-3.5 w-3.5 text-amber-400" />
          ) : (
            <Moon className="h-3.5 w-3.5 text-slate-700" />
          )}
        </Button>
      </div>
    </header>
  );
}

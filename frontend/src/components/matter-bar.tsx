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
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

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
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 bg-card/80 backdrop-blur-md px-4 py-2.5 sticky top-0 z-40">
      {/* Brand & Active Matter */}
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/" className="flex items-center gap-2 font-bold text-foreground">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Scale className="h-4 w-4" />
          </div>
          <span className="text-base tracking-tight font-extrabold text-primary">
            فَصْل
          </span>
        </Link>

        {/* Matter Selector using official shadcn Select */}
        <div className="w-64 sm:w-72">
          <Select
            value={matterId ? String(matterId) : undefined}
            onValueChange={(val) => onSelect(val ? Number(val) : null)}
          >
            <SelectTrigger className="h-9 bg-card">
              <SelectValue placeholder="اختر ملف قضية…" />
            </SelectTrigger>
            <SelectContent>
              {matters.map((m) => (
                <SelectItem key={m.id} value={String(m.id)}>
                  <div className="flex items-center gap-2">
                    <FolderOpen className="h-4 w-4 text-sky-500 shrink-0" />
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
          className="h-9 gap-1 text-xs font-semibold"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>ملف جديد</span>
        </Button>

        {/* Matter Details Badge */}
        {activeMatter && (
          <div className="hidden lg:flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="rounded-md bg-muted px-2 py-0.5 font-medium flex items-center gap-1">
              <MapPin className="h-3 w-3" />
              {activeMatter.jurisdiction}
            </span>
            <span className="rounded-md bg-muted px-2 py-0.5 font-medium flex items-center gap-1">
              <Briefcase className="h-3 w-3" />
              {activeMatter.matter_type}
            </span>
          </div>
        )}
      </div>

      {/* Global Actions: Spotlight Trigger, Library, Dark Mode */}
      <div className="flex items-center gap-2">
        {/* Spotlight Command Trigger Button */}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onOpenCommandPalette}
          className="h-9 gap-2 text-xs text-muted-foreground hover:text-foreground bg-muted/40"
        >
          <Search className="h-3.5 w-3.5 text-primary" />
          <span className="hidden sm:inline">بحث واستكشاف…</span>
          <kbd className="hidden sm:inline-flex items-center gap-0.5 rounded border border-border bg-card px-1.5 py-0.2 text-[10px] font-mono text-muted-foreground">
            ⌘K
          </kbd>
        </Button>

        {/* Library Link */}
        <Link href="/library">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-9 gap-1.5 text-xs font-semibold text-foreground"
          >
            <BookOpen className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
            <span className="hidden md:inline">المكتبة القانونية</span>
          </Button>
        </Link>

        {/* Theme Toggle */}
        <Button
          type="button"
          variant="outline"
          size="icon"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="h-9 w-9 text-foreground"
          aria-label="Toggle theme"
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4 text-amber-400" />
          ) : (
            <Moon className="h-4 w-4 text-slate-700" />
          )}
        </Button>
      </div>
    </header>
  );
}

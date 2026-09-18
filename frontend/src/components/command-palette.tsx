"use client";

import * as React from "react";
import {
  FolderOpen,
  BookOpen,
  Sparkles,
  FileText,
  Upload,
  Moon,
  Sun,
  Plus,
  Scale,
  MapPin,
  Check,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useRouter } from "next/navigation";
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import {
  listMatters,
  libraryCoverage,
  type Matter,
  type CoverageEntry,
} from "@/lib/api";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activeMatterId: number | null;
  onSelectMatter: (id: number) => void;
  onOpenNewMatterModal: () => void;
  onTriggerAction?: (action: "analysis" | "upload" | "draft" | "library") => void;
}

export function CommandPalette({
  open,
  onOpenChange,
  activeMatterId,
  onSelectMatter,
  onOpenNewMatterModal,
  onTriggerAction,
}: CommandPaletteProps) {
  const [matters, setMatters] = React.useState<Matter[]>([]);
  const [coverage, setCoverage] = React.useState<CoverageEntry[]>([]);
  const { theme, setTheme } = useTheme();
  const router = useRouter();

  // Load matters and library coverage when dialog opens
  React.useEffect(() => {
    if (open) {
      listMatters()
        .then(setMatters)
        .catch(() => setMatters([]));
      libraryCoverage()
        .then((c) => setCoverage(c.titles ?? []))
        .catch(() => setCoverage([]));
    }
  }, [open]);

  // Global shortcut listeners: Cmd+K, Ctrl+K, Cmd+Space
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        (e.key === "k" && (e.metaKey || e.ctrlKey)) ||
        (e.code === "Space" && (e.metaKey || e.altKey))
      ) {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="ابحث عن ملف، نص قانوني، أو إجراء… / Rechercher (⌘K)" />
      <CommandList>
        <CommandEmpty>لا توجد نتائج مطابقة للبحث.</CommandEmpty>

        {/* 1. Quick Actions */}
        <CommandGroup heading="⚡ إجراءات سريعة / Actions">
          <CommandItem
            onSelect={() => {
              onOpenChange(false);
              onOpenNewMatterModal();
            }}
          >
            <Plus className="me-2 h-4 w-4 text-emerald-500" />
            <span>إنشاء ملف قضية جديد / Nouveau dossier...</span>
            <CommandShortcut>⌘N</CommandShortcut>
          </CommandItem>

          {activeMatterId !== null && (
            <>
              <CommandItem
                onSelect={() => {
                  onOpenChange(false);
                  onTriggerAction?.("analysis");
                }}
              >
                <Sparkles className="me-2 h-4 w-4 text-amber-500" />
                <span>تشغيل التحليل الشامل للملف / Analyser</span>
                <CommandShortcut>AI</CommandShortcut>
              </CommandItem>

              <CommandItem
                onSelect={() => {
                  onOpenChange(false);
                  onTriggerAction?.("upload");
                }}
              >
                <Upload className="me-2 h-4 w-4 text-sky-500" />
                <span>رفع وثيقة جديدة / Téléverser document</span>
              </CommandItem>

              <CommandItem
                onSelect={() => {
                  onOpenChange(false);
                  onTriggerAction?.("draft");
                }}
              >
                <FileText className="me-2 h-4 w-4 text-purple-500" />
                <span>إنشاء مسودة قانونية / Générer acte ou avis</span>
              </CommandItem>
            </>
          )}

          <CommandItem
            onSelect={() => {
              onOpenChange(false);
              router.push("/library");
            }}
          >
            <Scale className="me-2 h-4 w-4 text-emerald-500" />
            <span>تصفح المكتبة القانونية / Explorer bibliothèque</span>
          </CommandItem>

          <CommandItem
            onSelect={() => {
              setTheme(theme === "dark" ? "light" : "dark");
              onOpenChange(false);
            }}
          >
            {theme === "dark" ? (
              <Sun className="me-2 h-4 w-4 text-amber-400" />
            ) : (
              <Moon className="me-2 h-4 w-4 text-indigo-400" />
            )}
            <span>{theme === "dark" ? "الوضع الفاتح" : "الوضع الليلي"}</span>
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        {/* 2. Matters */}
        {matters.length > 0 && (
          <CommandGroup heading="📁 ملفات القضايا / Dossiers">
            {matters.map((m) => (
              <CommandItem
                key={m.id}
                onSelect={() => {
                  onSelectMatter(m.id);
                  onOpenChange(false);
                }}
              >
                <FolderOpen className="me-2 h-4 w-4 text-sky-500" />
                <div className="flex flex-col min-w-0">
                  <span className="font-medium truncate">
                    #{m.id} {m.title}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {m.jurisdiction} · {m.matter_type}
                  </span>
                </div>
                {activeMatterId === m.id && (
                  <Check className="ms-auto h-4 w-4 text-emerald-500" />
                )}
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        <CommandSeparator />

        {/* 3. Legal Codes & Authorities */}
        {coverage.length > 0 && (
          <CommandGroup heading="📚 النصوص والمدونات القانونية / Textes juridiques">
            {coverage.map((c, idx) => (
              <CommandItem
                key={idx}
                onSelect={() => {
                  onOpenChange(false);
                  router.push("/library");
                }}
              >
                <BookOpen className="me-2 h-4 w-4 text-amber-500" />
                <div className="flex flex-col min-w-0">
                  <span className="font-medium truncate">
                    {c.source} — {c.version}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    {c.edition} {c.coverage_note ? `· ${c.coverage_note}` : ""}
                  </span>
                </div>
                {c.chunks !== undefined && (
                  <span className="ms-auto text-xs text-muted-foreground font-mono">
                    {c.chunks} فقرة
                  </span>
                )}
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}

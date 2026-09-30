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
  Globe,
  Check,
  MessageSquare,
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
  listConversations,
  type Matter,
  type CoverageEntry,
  type ConversationSummary,
} from "@/lib/api";
import { useI18n, SUPPORTED_LANGUAGES } from "@/lib/i18n";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activeMatterId: number | null;
  onSelectMatter: (id: number) => void;
  onSelectConversation?: (conversationId: number, matterId: number | null) => void;
  onOpenNewMatterModal: () => void;
  onTriggerAction?: (action: "analysis" | "upload" | "draft" | "library") => void;
}

function formatConvDate(dateStr: string, lang: string): string {
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString(
      lang === "ar" ? "ar-MA" : lang === "fr" ? "fr-FR" : "en-US",
      {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      },
    );
  } catch {
    return "";
  }
}

export function CommandPalette({
  open,
  onOpenChange,
  activeMatterId,
  onSelectMatter,
  onSelectConversation,
  onOpenNewMatterModal,
  onTriggerAction,
}: CommandPaletteProps) {
  const [matters, setMatters] = React.useState<Matter[]>([]);
  const [conversations, setConversations] = React.useState<ConversationSummary[]>([]);
  const [coverage, setCoverage] = React.useState<CoverageEntry[]>([]);
  const { theme, setTheme } = useTheme();
  const { t, language, setLanguage } = useI18n();
  const router = useRouter();

  // Load matters, conversations and library coverage when dialog opens
  React.useEffect(() => {
    if (open) {
      listMatters()
        .then(setMatters)
        .catch(() => setMatters([]));
      listConversations()
        .then(setConversations)
        .catch(() => setConversations([]));
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
      <CommandInput placeholder={t.commandPalette.searchPlaceholder} />
      <CommandList>
        <CommandEmpty>{t.commandPalette.emptyResults}</CommandEmpty>

        {/* 1. Quick Actions */}
        <CommandGroup heading={t.commandPalette.quickActionsHeading}>
          <CommandItem
            onSelect={() => {
              onOpenChange(false);
              onOpenNewMatterModal();
            }}
          >
            <Plus className="me-2 h-3.5 w-3.5 text-emerald-500 shrink-0" />
            <span className="text-xs">{t.commandPalette.newMatter}</span>
            <CommandShortcut className="text-[10px]">⌘N</CommandShortcut>
          </CommandItem>

          {activeMatterId !== null && (
            <>
              <CommandItem
                onSelect={() => {
                  onOpenChange(false);
                  onTriggerAction?.("analysis");
                }}
              >
                <Sparkles className="me-2 h-3.5 w-3.5 text-amber-500 shrink-0" />
                <span className="text-xs">{t.commandPalette.triggerAnalysis}</span>
                <CommandShortcut className="text-[10px]">AI</CommandShortcut>
              </CommandItem>

              <CommandItem
                onSelect={() => {
                  onOpenChange(false);
                  onTriggerAction?.("upload");
                }}
              >
                <Upload className="me-2 h-3.5 w-3.5 text-sky-500 shrink-0" />
                <span className="text-xs">{t.commandPalette.triggerUpload}</span>
              </CommandItem>

              <CommandItem
                onSelect={() => {
                  onOpenChange(false);
                  onTriggerAction?.("draft");
                }}
              >
                <FileText className="me-2 h-3.5 w-3.5 text-purple-500 shrink-0" />
                <span className="text-xs">{t.commandPalette.triggerDraft}</span>
              </CommandItem>
            </>
          )}

          <CommandItem
            onSelect={() => {
              onOpenChange(false);
              router.push("/library");
            }}
          >
            <Scale className="me-2 h-3.5 w-3.5 text-emerald-500 shrink-0" />
            <span className="text-xs">{t.commandPalette.openLibrary}</span>
          </CommandItem>

          <CommandItem
            onSelect={() => {
              setTheme(theme === "dark" ? "light" : "dark");
              onOpenChange(false);
            }}
          >
            {theme === "dark" ? (
              <Sun className="me-2 h-3.5 w-3.5 text-amber-400 shrink-0" />
            ) : (
              <Moon className="me-2 h-3.5 w-3.5 text-indigo-400 shrink-0" />
            )}
            <span className="text-xs">
              {theme === "dark"
                ? t.commandPalette.switchToLight
                : t.commandPalette.switchToDark}
            </span>
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        {/* 2. Old / Past Conversations */}
        {conversations.length > 0 && (
          <>
            <CommandGroup heading={t.commandPalette.conversationsHeading}>
              {conversations.map((conv) => {
                const dateStr = formatConvDate(conv.created_at, language);
                const msgCountText = t.commandPalette.messagesCount.replace(
                  "{count}",
                  String(conv.message_count),
                );
                return (
                  <CommandItem
                    key={`conv-${conv.id}`}
                    value={`conversation ${conv.title} ${conv.matter_title ?? ""} ${conv.preview ?? ""}`}
                    onSelect={() => {
                      onOpenChange(false);
                      onSelectConversation?.(conv.id, conv.matter_id);
                    }}
                  >
                    <MessageSquare className="me-2 h-3.5 w-3.5 text-primary shrink-0" />
                    <div className="flex flex-col min-w-0 flex-1 py-0.5">
                      <span className="text-xs font-medium truncate text-foreground leading-snug">
                        {conv.title}
                      </span>
                      <span className="text-[11px] text-muted-foreground truncate leading-tight mt-0.5">
                        {conv.matter_title ? `${conv.matter_title} · ` : ""}
                        {msgCountText}
                        {dateStr ? ` · ${dateStr}` : ""}
                      </span>
                      {conv.preview && conv.preview !== conv.title && (
                        <span className="text-[10px] text-muted-foreground/80 truncate mt-0.5 leading-tight italic">
                          &ldquo;{conv.preview}&rdquo;
                        </span>
                      )}
                    </div>
                  </CommandItem>
                );
              })}
            </CommandGroup>
            <CommandSeparator />
          </>
        )}

        {/* 3. Matters */}
        {matters.length > 0 && (
          <>
            <CommandGroup heading={t.commandPalette.mattersHeading}>
              {matters.map((m) => (
                <CommandItem
                  key={m.id}
                  value={`matter ${m.id} ${m.title} ${m.jurisdiction} ${m.matter_type}`}
                  onSelect={() => {
                    onSelectMatter(m.id);
                    onOpenChange(false);
                  }}
                >
                  <FolderOpen className="me-2 h-3.5 w-3.5 text-sky-500 shrink-0" />
                  <div className="flex flex-col min-w-0 flex-1 py-0.5">
                    <span className="text-xs font-medium truncate leading-snug">
                      #{m.id} {m.title}
                    </span>
                    <span className="text-[11px] text-muted-foreground leading-tight mt-0.5">
                      {m.jurisdiction} · {m.matter_type}
                    </span>
                  </div>
                  {activeMatterId === m.id && (
                    <Check className="ms-auto h-3.5 w-3.5 text-emerald-500 shrink-0" />
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
            <CommandSeparator />
          </>
        )}

        {/* 4. Legal Codes & Authorities */}
        {coverage.length > 0 && (
          <>
            <CommandGroup heading={t.commandPalette.libraryHeading}>
              {coverage.map((c, idx) => (
                <CommandItem
                  key={idx}
                  value={`legal code authority ${c.source} ${c.version} ${c.edition} ${c.coverage_note ?? ""}`}
                  onSelect={() => {
                    onOpenChange(false);
                    router.push("/library");
                  }}
                >
                  <BookOpen className="me-2 h-3.5 w-3.5 text-amber-500 shrink-0" />
                  <div className="flex flex-col min-w-0 flex-1 py-0.5">
                    <span className="text-xs font-medium truncate leading-snug">
                      {c.source} — {c.version}
                    </span>
                    <span className="text-[11px] text-muted-foreground truncate leading-tight mt-0.5">
                      {c.edition} {c.coverage_note ? `· ${c.coverage_note}` : ""}
                    </span>
                  </div>
                  {c.chunks !== undefined && (
                    <span className="ms-auto text-[10px] text-muted-foreground font-mono shrink-0">
                      {c.chunks} {t.upload.chunksIndexed}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
            <CommandSeparator />
          </>
        )}

        {/* 5. Language Switcher Group */}
        <CommandGroup heading={t.commandPalette.languagesHeading}>
          {SUPPORTED_LANGUAGES.map((lang) => {
            const isSelected = lang.code === language;
            return (
              <CommandItem
                key={lang.code}
                value={`language ${lang.label} ${lang.nativeLabel}`}
                onSelect={() => {
                  setLanguage(lang.code);
                  onOpenChange(false);
                }}
              >
                <Globe className="me-2 h-3.5 w-3.5 text-primary shrink-0" />
                <span className="text-xs">
                  {lang.flag} {lang.nativeLabel} ({lang.label})
                </span>
                {isSelected && (
                  <Check className="ms-auto h-3.5 w-3.5 text-emerald-500 shrink-0" />
                )}
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}

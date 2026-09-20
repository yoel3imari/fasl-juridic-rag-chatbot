"use client";

import { useState } from "react";
import { AnalysisPanel } from "@/components/analysis-panel";
import { Chat } from "@/components/chat";
import { DraftsPanel } from "@/components/drafts-panel";
import { MatterBar } from "@/components/matter-bar";
import { Upload } from "@/components/upload";
import { SourceInspector } from "@/components/source-inspector";
import { CommandPalette } from "@/components/command-palette";
import { NewMatterModal } from "@/components/new-matter-modal";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useI18n } from "@/lib/i18n";
import type { Citation, Matter, SpanRef } from "@/lib/api";
import {
  FileText,
  Sparkles,
  PenTool,
  BookOpen,
} from "lucide-react";

export default function Home() {
  const { t } = useI18n();
  const [matterId, setMatterId] = useState<number | null>(null);
  const [activeRightTab, setActiveRightTab] = useState<string>("documents");
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [newMatterModalOpen, setNewMatterModalOpen] = useState(false);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [activeSpan, setActiveSpan] = useState<SpanRef | null>(null);

  const handleSelectCitation = (citation: Citation) => {
    setActiveCitation(citation);
    setActiveSpan(null);
    setActiveRightTab("source");
  };

  const handleSelectSpan = (span: SpanRef) => {
    setActiveSpan(span);
    setActiveCitation(null);
    setActiveRightTab("source");
  };

  const handleMatterCreated = (created: Matter) => {
    setMatterId(created.id);
  };

  const handleTriggerAction = (action: "analysis" | "upload" | "draft" | "library") => {
    if (action === "analysis") setActiveRightTab("analysis");
    else if (action === "upload") setActiveRightTab("documents");
    else if (action === "draft") setActiveRightTab("drafts");
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      {/* Top Application Bar */}
      <MatterBar
        matterId={matterId}
        onSelect={setMatterId}
        onOpenNewMatterModal={() => setNewMatterModalOpen(true)}
        onOpenCommandPalette={() => setCommandPaletteOpen(true)}
      />

      {/* Main 2-Sided Split Workspace */}
      <main className="flex flex-1 overflow-hidden p-3 gap-3">
        {/* LEFT PANEL: Legal AI Chat */}
        <div className="flex-1 min-w-[320px] max-w-full lg:max-w-[48%] h-full">
          <Chat
            matterId={matterId}
            onSelectCitation={handleSelectCitation}
          />
        </div>

        {/* RIGHT PANEL: Active Matter Workspace (Documents, Analysis, Drafts, Source) */}
        <div className="hidden md:flex flex-1 flex-col h-full overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <Tabs value={activeRightTab} onValueChange={setActiveRightTab} className="flex flex-col h-full overflow-hidden">
            {/* Workspace Tabs Header */}
            <div className="border-b border-border/80 px-3 py-2 bg-muted/20 overflow-x-auto overflow-y-hidden">
              <TabsList className="bg-muted/60 h-9 p-1 flex-nowrap inline-flex w-max shrink-0 gap-1">
                <TabsTrigger value="documents" className="gap-1.5 text-xs shrink-0 whitespace-nowrap">
                  <FileText className="h-3.5 w-3.5 text-sky-500 shrink-0" />
                  <span>{t.tabs.documents}</span>
                </TabsTrigger>
                <TabsTrigger value="analysis" className="gap-1.5 text-xs shrink-0 whitespace-nowrap">
                  <Sparkles className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                  <span>{t.tabs.analysis}</span>
                </TabsTrigger>
                <TabsTrigger value="drafts" className="gap-1.5 text-xs shrink-0 whitespace-nowrap">
                  <PenTool className="h-3.5 w-3.5 text-purple-500 shrink-0" />
                  <span>{t.tabs.drafts}</span>
                </TabsTrigger>
                <TabsTrigger value="source" className="gap-1.5 text-xs shrink-0 whitespace-nowrap">
                  <BookOpen className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                  <span>{t.tabs.source}</span>
                </TabsTrigger>
              </TabsList>
            </div>

            {/* Scrollable Workspace Panels */}
            <div className="flex-1 overflow-y-auto p-4 max-h-[calc(100vh-120px)] pe-2">
              <TabsContent value="documents" className="mt-0 focus-visible:outline-none">
                <Upload matterId={matterId} />
              </TabsContent>

              <TabsContent value="analysis" className="mt-0 focus-visible:outline-none">
                <AnalysisPanel
                  matterId={matterId}
                  onSelectSpan={handleSelectSpan}
                />
              </TabsContent>

              <TabsContent value="drafts" className="mt-0 focus-visible:outline-none">
                <DraftsPanel matterId={matterId} />
              </TabsContent>

              <TabsContent value="source" className="mt-0 focus-visible:outline-none">
                <SourceInspector
                  activeCitation={activeCitation}
                  activeSpan={activeSpan}
                />
              </TabsContent>
            </div>
          </Tabs>
        </div>
      </main>

      {/* Global Command Palette (⌘K / ⌘Space) */}
      <CommandPalette
        open={commandPaletteOpen}
        onOpenChange={setCommandPaletteOpen}
        activeMatterId={matterId}
        onSelectMatter={(id) => setMatterId(id)}
        onOpenNewMatterModal={() => setNewMatterModalOpen(true)}
        onTriggerAction={handleTriggerAction}
      />

      {/* New Matter Modal */}
      <NewMatterModal
        open={newMatterModalOpen}
        onOpenChange={setNewMatterModalOpen}
        onMatterCreated={handleMatterCreated}
      />
    </div>
  );
}

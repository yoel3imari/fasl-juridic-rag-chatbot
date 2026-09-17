"use client";

import { useTheme } from "next-themes";
import Link from "next/link";
import { useState } from "react";
import { AnalysisPanel } from "@/components/analysis-panel";
import { Chat } from "@/components/chat";
import { DraftsPanel } from "@/components/drafts-panel";
import { MatterBar } from "@/components/matter-bar";
import { Upload } from "@/components/upload";

export default function Home() {
  const [matterId, setMatterId] = useState<number | null>(null);
  const { theme, setTheme } = useTheme();

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 p-4 sm:p-6">
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">مساعدك القانوني / Assistant juridique</h1>
        <div className="flex items-center gap-2 text-sm">
          <Link href="/library" className="underline">
            المكتبة / Bibliothèque
          </Link>
          <button
            type="button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="rounded-md border border-neutral-300 px-2 py-1 dark:border-neutral-700"
            aria-label="toggle dark mode"
          >
            ◐
          </button>
        </div>
      </header>
      <MatterBar matterId={matterId} onSelect={setMatterId} />
      <Upload matterId={matterId} />
      <Chat matterId={matterId} />
      <AnalysisPanel matterId={matterId} />
      <DraftsPanel matterId={matterId} />
    </main>
  );
}

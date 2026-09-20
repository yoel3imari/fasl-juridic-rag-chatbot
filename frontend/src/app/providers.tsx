"use client";

import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";
import { I18nProvider } from "@/lib/i18n";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <I18nProvider>{children}</I18nProvider>
    </ThemeProvider>
  );
}

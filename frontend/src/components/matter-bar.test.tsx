import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MatterBar } from "./matter-bar";
import { I18nProvider } from "@/lib/i18n";
import * as api from "@/lib/api";
import type { Matter } from "@/lib/api";

// MatterBar pulls in LanguageSwitcher, which reads the theme.
vi.mock("next-themes", () => ({
  useTheme: () => ({
    theme: "light",
    setTheme: vi.fn(),
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listMatters: vi.fn(),
    deleteMatter: vi.fn(),
  };
});

const listMatters = vi.mocked(api.listMatters);

const newMatter: Matter = {
  id: 5,
  title: "New matter",
  matter_type: "labor",
  jurisdiction: "casablanca",
  language: "ar",
};

function renderBar(refreshToken: number) {
  return render(
    <I18nProvider>
      <MatterBar
        matterId={null}
        onSelect={() => {}}
        onOpenNewMatterModal={() => {}}
        onOpenCommandPalette={() => {}}
        onMatterDeleted={() => {}}
        refreshToken={refreshToken}
      />
    </I18nProvider>,
  );
}

describe("MatterBar matter list refresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("fasl_language", "en");
  });

  it("re-fetches matters when refreshToken changes so a new matter appears", async () => {
    listMatters.mockResolvedValueOnce([]);
    const { rerender } = renderBar(0);
    await waitFor(() => expect(listMatters).toHaveBeenCalledTimes(1));

    listMatters.mockResolvedValueOnce([newMatter]);
    rerender(
      <I18nProvider>
        <MatterBar
          matterId={null}
          onSelect={() => {}}
          onOpenNewMatterModal={() => {}}
          onOpenCommandPalette={() => {}}
          onMatterDeleted={() => {}}
          refreshToken={1}
        />
      </I18nProvider>,
    );

    await waitFor(() => expect(listMatters).toHaveBeenCalledTimes(2));
  });
});

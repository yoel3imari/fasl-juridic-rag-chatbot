import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { CommandPalette } from "./command-palette";
import { I18nProvider } from "@/lib/i18n";
import * as api from "@/lib/api";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

// Mock next-themes
vi.mock("next-themes", () => ({
  useTheme: () => ({
    theme: "light",
    setTheme: vi.fn(),
  }),
}));

// Mock API calls
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listMatters: vi.fn().mockResolvedValue([
      {
        id: 1,
        title: "Atlas SARL Dispute",
        matter_type: "labor",
        jurisdiction: "casablanca",
        language: "ar",
      },
    ]),
    listConversations: vi.fn().mockResolvedValue([
      {
        id: 101,
        matter_id: 1,
        matter_title: "Atlas SARL Dispute",
        title: "Article 62 notice inquiry",
        created_at: "2026-09-22T10:00:00Z",
        message_count: 3,
        preview: "Was the disciplinary procedure followed properly?",
      },
    ]),
    libraryCoverage: vi.fn().mockResolvedValue({
      titles: [
        {
          source: "Code du travail",
          version: "2024",
          edition: "ar-general",
          chunks: 450,
        },
      ],
      gaps: [],
      library_version: "1.0.0",
    }),
  };
});

describe("CommandPalette with Conversations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads and displays past conversations in the search modal", async () => {
    const onSelectConversation = vi.fn();
    const onSelectMatter = vi.fn();
    const onOpenChange = vi.fn();

    render(
      <I18nProvider>
        <CommandPalette
          open={true}
          onOpenChange={onOpenChange}
          activeMatterId={1}
          onSelectMatter={onSelectMatter}
          onSelectConversation={onSelectConversation}
          onOpenNewMatterModal={vi.fn()}
        />
      </I18nProvider>,
    );

    // Wait for conversation items to appear
    await waitFor(() => {
      expect(screen.getByText("Article 62 notice inquiry")).toBeInTheDocument();
    });

    // Check preview snippet or matter title
    expect(screen.getAllByText(/Atlas SARL Dispute/i).length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText(/Was the disciplinary procedure followed properly\?/i),
    ).toBeInTheDocument();

    // Click on the conversation item
    const convItem = screen.getByText("Article 62 notice inquiry");
    fireEvent.click(convItem);

    expect(onSelectConversation).toHaveBeenCalledWith(101, 1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

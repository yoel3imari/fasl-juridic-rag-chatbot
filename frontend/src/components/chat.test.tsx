/**
 * TDD (red-first) for chat → right-panel chunk notification.
 *
 * Given: retrieved chunks are no longer rendered in the chat stream; they are
 *        listed in the right panel's source section.
 * When: a live answer retrieves chunks.
 * Then: the host is told so it can activate that section, and loading a past
 *       conversation does not trigger it — replaying history must not pull the
 *       reader away from whatever they were reading.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AuthorityCitation,
  Citation,
  ConversationDetail,
  LlmSettings,
  ModelsResponse,
  SseEvent,
} from "@/lib/api";
import { Chat } from "./chat";

const { mockStreamChat, mockGetConversation } = vi.hoisted(() => ({
  mockStreamChat: vi.fn(),
  mockGetConversation: vi.fn(),
}));

const SETTINGS: LlmSettings = {
  current_provider: "ollama",
  current_model: "llama3.2",
  privacy_mode: "strict",
  keys_status: {},
  masked_keys: {},
};

const MODELS: ModelsResponse = {
  current_provider: "ollama",
  current_model: "llama3.2",
  privacy_mode: "strict",
  providers: [],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    streamChat: mockStreamChat,
    getConversation: mockGetConversation,
    getLlmSettings: vi.fn(async () => SETTINGS),
    listChatModels: vi.fn(async () => MODELS),
    saveLlmSettings: vi.fn(async () => SETTINGS),
  };
});

const { I18nProvider } = await import("@/lib/i18n");

const ARTICLE: AuthorityCitation = {
  domain: "authority",
  source: "Code du Travail",
  version: "2023",
  edition: "ar-general",
  pub_date: null,
  doc_date: null,
  language: "ar",
  article_or_section: "Article 237",
  excerpt: "Préavis de trente jours.",
};

function stream(...events: SseEvent[]): AsyncGenerator<SseEvent, void, void> {
  return (async function* () {
    for (const event of events) yield event;
  })();
}

function renderChat(props: {
  conversationId?: number | null;
  onChunksRetrieved?: () => void;
  onRetrievedChunks?: (chunks: Citation[]) => void;
}) {
  const ui = (conversationId: number | null) => (
    <I18nProvider>
      <Chat
        matterId={1}
        conversationId={conversationId}
        onChunksRetrieved={props.onChunksRetrieved}
        onRetrievedChunks={props.onRetrievedChunks}
      />
    </I18nProvider>
  );
  const view = render(ui(props.conversationId ?? null));
  return { ...view, showConversation: (id: number) => view.rerender(ui(id)) };
}

async function ask(question: string) {
  const input = screen.getByLabelText(/question/i);
  fireEvent.change(input, { target: { value: question } });
  // The SSE generator resolves across microtasks, unlike every other fireEvent here.
  await act(async () => {
    fireEvent.submit(input.closest("form") as HTMLFormElement);
  });
}

describe("Chat chunk notification", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("announces chunks when a live answer retrieves them", async () => {
    mockStreamChat.mockReturnValue(
      stream({ type: "citations", citations: [ARTICLE] }, { type: "done" }),
    );
    const onChunksRetrieved = vi.fn();
    const onRetrievedChunks = vi.fn();
    renderChat({ onChunksRetrieved, onRetrievedChunks });

    await ask("ما هي مهلة الإخطار؟");

    await waitFor(() => expect(onChunksRetrieved).toHaveBeenCalledTimes(1));
    expect(onRetrievedChunks).toHaveBeenCalledWith([ARTICLE]);
  });

  it("stays quiet when retrieval came back empty", async () => {
    mockStreamChat.mockReturnValue(
      stream({ type: "citations", citations: [] }, { type: "done" }),
    );
    const onChunksRetrieved = vi.fn();
    renderChat({ onChunksRetrieved });

    await ask("سؤال بلا سند");

    await waitFor(() => expect(mockStreamChat).toHaveBeenCalled());
    expect(onChunksRetrieved).not.toHaveBeenCalled();
  });

  it("replays history into the panel without hijacking the section", async () => {
    const detail: ConversationDetail = {
      id: 7,
      matter_id: 1,
      title: "past",
      created_at: "2026-01-01T00:00:00Z",
      messages: [
        {
          id: 1,
          conversation_id: 7,
          role: "user",
          content: "س",
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: 2,
          conversation_id: 7,
          role: "assistant",
          content: "إجابة",
          citations_json: [ARTICLE],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    };
    mockGetConversation.mockResolvedValue(detail);
    const onChunksRetrieved = vi.fn();
    const onRetrievedChunks = vi.fn();

    const { showConversation } = renderChat({ onChunksRetrieved, onRetrievedChunks });
    showConversation(7);

    await waitFor(() => expect(onRetrievedChunks).toHaveBeenCalledWith([ARTICLE]));
    expect(onChunksRetrieved).not.toHaveBeenCalled();
    expect(mockStreamChat).not.toHaveBeenCalled();
  });
});
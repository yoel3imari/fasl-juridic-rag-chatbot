"use client";

import { useCallback, useRef, useState, useEffect } from "react";
import {
  ApiError,
  streamChat,
  listChatModels,
  getLlmSettings,
  saveLlmSettings,
  getConversation,
  type Citation,
  type SseEvent,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { MarkdownRenderer } from "./markdown-renderer";
import { ModelSwitcherModal } from "./model-switcher-modal";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Send,
  Square,
  Sparkles,
  AlertCircle,
  AlertTriangle,
  Scale,
  Copy,
  Check,
  ChevronDown,
  ShieldCheck,
  Cloud,
  MessageSquare,
  Plus,
  ArrowRightIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Message {
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
  notFound?: boolean;
  outOfScope?: boolean;
  error?: string;
}

const PROVIDER_STORAGE_KEY = "fasl_llm_provider";
const MODEL_STORAGE_KEY = "fasl_llm_model";

interface ChatProps {
  matterId: number | null;
  conversationId?: number | null;
  onConversationChange?: (id: number | null) => void;
  /** Chunks are browsed in the right panel, so the stream never renders them. */
  onRetrievedChunks?: (chunks: Citation[]) => void;
  /**
   * Fired once per live retrieval, when chunks first land. History replay is
   * deliberately excluded: re-opening a past conversation must not yank the
   * reader out of whatever right-panel tab they were on.
   */
  onChunksRetrieved?: () => void;
}

export function Chat({
  matterId,
  conversationId = null,
  onConversationChange,
  onRetrievedChunks,
  onChunksRetrieved,
}: ChatProps) {
  const { t } = useI18n();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [provider, setProvider] = useState<string>("ollama");
  const [model, setModel] = useState<string>("llama3.2");
  const [modelModalOpen, setModelModalOpen] = useState(false);
  const [keysStatus, setKeysStatus] = useState<Record<string, boolean> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const activeConvIdRef = useRef<number | null>(conversationId ?? null);
  const prevConvIdRef = useRef<number | null>(conversationId ?? null);

  // Load LLM settings
  useEffect(() => {
    let cancelled = false;
    getLlmSettings()
      .then((res) => {
        if (cancelled) return;
        if (res.current_provider) setProvider(res.current_provider);
        if (res.current_model) setModel(res.current_model);
        setKeysStatus(res.keys_status ?? {});
        try {
          if (res.current_provider)
            localStorage.setItem(PROVIDER_STORAGE_KEY, res.current_provider);
          if (res.current_model)
            localStorage.setItem(MODEL_STORAGE_KEY, res.current_model);
        } catch {
          // LocalStorage unavailable
        }
      })
      .catch(() => {
        if (cancelled) return;
        try {
          const storedProvider = localStorage.getItem(PROVIDER_STORAGE_KEY);
          const storedModel = localStorage.getItem(MODEL_STORAGE_KEY);
          if (storedProvider) setProvider(storedProvider);
          if (storedModel) setModel(storedModel);

          if (!storedProvider || !storedModel) {
            listChatModels()
              .then((res) => {
                if (cancelled) return;
                if (!storedProvider && res.current_provider)
                  setProvider(res.current_provider);
                if (!storedModel && res.current_model)
                  setModel(res.current_model);
              })
              .catch(() => {});
          }
        } catch {
          // LocalStorage unavailable
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load past conversation messages when conversationId changes
  useEffect(() => {
    let cancelled = false;
    const prevId = prevConvIdRef.current;
    prevConvIdRef.current = conversationId ?? null;

    // If conversationId didn't actually change, do nothing
    if (prevId === (conversationId ?? null)) {
      return;
    }

    // If this change was from the current active stream establishing the conversation, do not reload
    if (
      conversationId !== null &&
      conversationId !== undefined &&
      activeConvIdRef.current === conversationId
    ) {
      return;
    }

    if (conversationId !== null && conversationId !== undefined) {
      activeConvIdRef.current = conversationId;
      getConversation(conversationId)
        .then((detail) => {
          if (cancelled) return;
          const loaded: Message[] = detail.messages.map((m) => ({
            role: m.role as "user" | "assistant",
            text: m.content,
            citations: (m.citations_json as Citation[]) || [],
            error:
              m.role === "assistant" && !m.content
                ? t.chat.errorNetwork
                : undefined,
          }));
          setMessages(loaded);
          setActivity(null);
        })
        .catch((err) => {
          if (cancelled) return;
          setToast(
            err instanceof ApiError
              ? `Failed to load conversation: ${err.message}`
              : "Failed to load conversation",
          );
        });
    } else {
      activeConvIdRef.current = null;
      setMessages([]);
      setActivity(null);
    }
    return () => {
      cancelled = true;
    };
  }, [conversationId, t]);

  const handleModelChange = (newProvider: string, newModel: string) => {
    setProvider(newProvider);
    setModel(newModel);
    try {
      localStorage.setItem(PROVIDER_STORAGE_KEY, newProvider);
      localStorage.setItem(MODEL_STORAGE_KEY, newModel);
    } catch {}
    saveLlmSettings({ provider: newProvider, model: newModel })
      .then((res) => {
        if (res.keys_status) setKeysStatus(res.keys_status);
      })
      .catch(() => {});
  };

  const handleNewChat = () => {
    abortRef.current?.abort();
    activeConvIdRef.current = null;
    prevConvIdRef.current = null;
    setMessages([]);
    setActivity(null);
    setToast(null);
    onConversationChange?.(null);
  };

  const isCloudProvider = provider !== "ollama";
  const activeProviderHasKey =
    keysStatus !== null ? (keysStatus[provider] ?? false) : null;

  const scrollToBottom = () => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop =
        scrollContainerRef.current.scrollHeight;
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, busy, activity]);

  // The stream rewrites `messages` on every token, so lifting a fresh array
  // each time would re-render the whole right panel per token. Citations keep
  // their identity across tokens (they only land on a `citations` event), so
  // reference-equality is enough to gate the lift on real retrieval changes.
  const liftedChunksRef = useRef<Citation[] | null>(null);
  useEffect(() => {
    const chunks = messages.flatMap((m) => m.citations);
    const previous = liftedChunksRef.current;
    if (previous && previous.length === chunks.length && previous.every((c, i) => c === chunks[i])) {
      return;
    }
    liftedChunksRef.current = chunks;
    onRetrievedChunks?.(chunks);
  }, [messages, onRetrievedChunks]);

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const send = useCallback(
    async (overrideText?: string) => {
      const content = (overrideText ?? input).trim();
      if (!content || busy) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setInput("");
      setToast(null);
      setBusy(true);
      setActivity(null);
      setMessages((m) => [
        ...m,
        { role: "user", text: content, citations: [] },
        { role: "assistant", text: "", citations: [] },
      ]);

      let citations: Citation[] = [];
      let text = "";
      let error: string | undefined;
      let notFound = false;
      let outOfScope = false;

      const apply = () =>
        setMessages((m) => {
          const next = [...m];
          next[next.length - 1] = {
            role: "assistant",
            text,
            citations,
            notFound,
            outOfScope,
            error,
          };
          return next;
        });

      const onEvent = (ev: SseEvent) => {
        if (ev.type === "citations") {
          citations = ev.citations;
          if (citations.length > 0) onChunksRetrieved?.();
        } else if (ev.type === "token") text += ev.text;
        else if (ev.type === "done") {
          notFound = ev.not_found ?? false;
          outOfScope = ev.out_of_scope ?? false;
          setActivity(null);
          if (ev.conversation_id) {
            activeConvIdRef.current = ev.conversation_id;
            onConversationChange?.(ev.conversation_id);
          }
        } else if (ev.type === "error") {
          error = `${ev.code}: ${ev.detail}`;
          setActivity(null);
          if (ev.conversation_id) {
            activeConvIdRef.current = ev.conversation_id;
            onConversationChange?.(ev.conversation_id);
          }
        } else if (ev.type === "status") setActivity(ev.message || ev.stage);
        apply();
      };

      try {
        for await (const ev of streamChat(matterId, content, {
          conversationId,
          signal: controller.signal,
          provider,
          model,
        })) {
          onEvent(ev);
        }
        if (!text && !error) {
          error = t.chat.errorNetwork;
          apply();
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          error = t.chat.errorAbort;
        } else if (e instanceof ApiError) {
          error = `${e.status}: ${e.message}`;
          setToast(t.chat.errorServer.replace("{status}", String(e.status)));
        } else {
          error = e instanceof Error ? e.message : "request failed";
          setToast(t.chat.errorNetwork);
        }
        apply();
      } finally {
        setActivity(null);
        setBusy(false);
      }
    },
    [input, busy, matterId, conversationId, t, provider, model, onConversationChange, onChunksRetrieved],
  );

  return (
    <section
      aria-label="chat"
      className="relative flex h-full flex-col overflow-hidden rounded-2xl border border-border/80 bg-card/60 backdrop-blur-sm shadow-xs"
    >
      {/* Modern Top Header with Current Provider/Model Indicator, Conversation Badge & Actions */}
      <div className="flex items-center justify-between gap-2 border-b border-border/60 bg-card/40 px-3.5 py-2 shrink-0">
        <div className="flex items-center gap-1.5 flex-wrap min-w-0">
          {/* Current Provider & Model Indicator Button */}
          <button
            type="button"
            onClick={() => setModelModalOpen(true)}
            title={t.modelSwitcher.switchModelButton}
            aria-label={t.modelSwitcher.switchModelButton}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border/70 bg-background/80 hover:bg-muted/80 px-2.5 py-1 text-xs text-foreground shadow-2xs transition-all cursor-pointer hover:border-primary/40 group shrink-0"
          >
            {/* <span
              className={cn(
                "h-2 w-2 rounded-full shrink-0 animate-pulse",
                provider === "ollama" ? "bg-emerald-500" : "bg-sky-500",
              )}
            /> */}
            {provider === "ollama" ? (
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
            ) : (
              <Cloud className="h-3.5 w-3.5 text-sky-600 dark:text-sky-400 shrink-0" />
            )}
            <span className="text-xs font-medium capitalize truncate">
              {provider} · <span className="font-mono text-[11px] text-muted-foreground">{model}</span>
            </span>
            <ChevronDown className="h-3 w-3 text-muted-foreground group-hover:text-foreground transition-colors shrink-0" />
          </button>

          {conversationId && (
            <Badge
              variant="outline"
              className="text-[11px] px-2 py-0.5 border-primary/30 bg-primary/10 text-primary font-medium shrink-0 flex items-center gap-1"
            >
              <MessageSquare className="h-3 w-3" />
              <span>{t.chat.activeConversation.replace("{id}", String(conversationId))}</span>
            </Badge>
          )}

          {isCloudProvider && activeProviderHasKey === false && (
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0.5 border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium shrink-0"
            >
              <AlertTriangle className="h-2.5 w-2.5 me-0.5" />
              {t.modelSwitcher.apiKeyMissing}
            </Badge>
          )}
        </div>

        {/* Action button: New Chat */}
        <div className="flex items-center gap-1.5 shrink-0">
          {messages.length > 0 && (
            <button
              type="button"
              onClick={handleNewChat}
              title={t.chat.newChat}
              aria-label={t.chat.newChat}
              className="inline-flex items-center gap-1 rounded-lg border border-primary/30 bg-primary/10 hover:bg-primary/20 text-primary px-2.5 py-1 text-xs shadow-2xs transition-colors cursor-pointer"
            >
              <Plus className="h-3.5 w-3.5" />
              <span className="text-[11px] font-medium">{t.chat.newChat}</span>
            </button>
          )}
        </div>
      </div>

      {/* Alert Toast */}
      {toast && (
        <div
          role="alert"
          className="mx-4 mt-3 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive font-medium animate-in fade-in shrink-0"
        >
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{toast}</span>
        </div>
      )}

      {/* Chat Messages Stream */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-6"
      >
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center py-8 text-center animate-in fade-in duration-300">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary mb-3.5 shadow-2xs">
              <Scale className="h-6 w-6" />
            </div>
            <h3 className="text-base font-semibold tracking-tight text-foreground">
              {t.chat.welcomeTitle}
            </h3>
            <p className="mt-1.5 max-w-sm text-xs text-muted-foreground leading-relaxed">
              {t.chat.welcomeSubtitle}
            </p>

            {/* Quick Starter Chips */}
            <div className="mt-6 flex flex-col gap-2 w-full max-w-md">
              <span className="text-[11px] font-medium text-muted-foreground/80 text-start px-0.5">
                {t.chat.suggestedPromptsLabel}
              </span>
              {t.chat.suggestedPrompts.map((prompt, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => void send(prompt)}
                  disabled={busy}
                  className="group flex items-center justify-between rounded-xl border border-border/60 bg-background/50 hover:bg-muted/60 hover:border-border p-3 text-start text-xs text-foreground transition-all cursor-pointer disabled:opacity-50 shadow-2xs"
                >
                  <span className="leading-relaxed">{prompt}</span>
                  <Sparkles className="h-3.5 w-3.5 text-primary/70 group-hover:text-primary shrink-0 ms-2 transition-colors" />
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={cn(
              "flex flex-col text-sm animate-in fade-in duration-200",
              m.role === "user" ? "items-end" : "items-start w-full",
            )}
          >
            {m.role === "user" ? (
              /* User message: clean modern bubble aligned to end */
              <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-primary/10 text-foreground dark:bg-primary/20 dark:text-foreground px-4 py-2.5 text-sm leading-relaxed border border-primary/15 shadow-2xs">
                <div className="whitespace-pre-wrap">{m.text}</div>
              </div>
            ) : (
              /* Assistant message: seamless integration, same background as chat canvas */
              <div className="w-full space-y-2 text-foreground">
                <div className="text-sm leading-relaxed">
                  {m.text ? (
                    <MarkdownRenderer content={m.text} />
                  ) : busy && i === messages.length - 1 && !m.error ? (
                    <span className="inline-flex items-center gap-2 text-xs text-muted-foreground animate-pulse py-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                      {t.chat.draftingStatus}
                    </span>
                  ) : !m.error ? (
                    <span className="text-xs text-destructive font-medium">
                      {t.chat.errorNetwork}
                    </span>
                  ) : null}
                </div>

                {/* Error */}
                {m.error && (
                  <div className="flex items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive font-medium">
                    <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                    <span>{m.error}</span>
                  </div>
                )}

                {/* Provisional disclaimer if not found */}
                {m.notFound && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                    {t.chat.provisionalDisclaimer}
                  </p>
                )}

                {/* Scope notice when the question was refused as out of scope */}
                {m.outOfScope && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                    {t.chat.outOfScopeNotice}
                  </p>
                )}

                {/* Actions & Copy */}
                {m.text && (
                  <div className="pt-0.5 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleCopy(m.text, i)}
                      className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer px-2 py-1 rounded-md transition-colors"
                    >
                      {copiedIndex === i ? (
                        <>
                          <Check className="h-3 w-3 text-emerald-500" />
                          <span className="text-emerald-500 font-medium">
                            {t.common.copied}
                          </span>
                        </>
                      ) : (
                        <>
                          <Copy className="h-3 w-3" />
                          <span>{t.common.copy}</span>
                        </>
                      )}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {busy && activity && (
          <div
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 px-1 py-1 text-xs text-muted-foreground animate-in fade-in"
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary animate-pulse" />
            <span className="text-[11px] font-medium">{activity}</span>
          </div>
        )}
      </div>

      {/* Input / Control Bar */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        className="p-3 pt-2 bg-transparent shrink-0"
      >
        <div className="relative flex items-center rounded-xl border border-border/80 bg-background/80 shadow-2xs transition-all focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/20 hover:border-border">
          <input
            aria-label="question"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
            placeholder={
              matterId
                ? t.chat.inputPlaceholderWithMatter
                : t.chat.inputPlaceholderNoMatter
            }
            className="flex-1 bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
          />

          <div className="flex items-center gap-1.5 pe-2.5">
            {busy ? (
              <Button
                type="button"
                size="sm"
                variant="destructive"
                onClick={() => abortRef.current?.abort()}
                className="h-8 gap-1.5 text-xs rounded-lg shadow-2xs font-medium"
              >
                <Square className="h-3 w-3 fill-current" />
                <span>{t.chat.stop}</span>
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                disabled={!input.trim()}
                className="h-8 w-8 rounded-lg shadow-2xs bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-30 transition-all"
              >
                <ArrowRightIcon className="h-3.5 w-3.5" />
                <span className="sr-only">{t.chat.send}</span>
              </Button>
            )}
          </div>
        </div>
      </form>

      {/* Model & Provider Switcher Modal */}
      <ModelSwitcherModal
        open={modelModalOpen}
        onOpenChange={setModelModalOpen}
        currentProvider={provider}
        currentModel={model}
        onSelectModel={handleModelChange}
      />
    </section>
  );
}

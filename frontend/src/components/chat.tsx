"use client";

import { useCallback, useRef, useState, useEffect } from "react";
import {
  ApiError,
  streamChat,
  type Citation,
  type SseEvent,
} from "@/lib/api";
import { CitationDomainBadge } from "./citation-domain-badge";
import { Button } from "@/components/ui/button";
import {
  Send,
  Square,
  Sparkles,
  AlertCircle,
  Scale,
  Bot,
  User,
  ShieldCheck,
  RefreshCw,
  Copy,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Message {
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
  notFound?: boolean;
  error?: string;
}

function eventKey(c: Citation, i: number): string {
  return c.domain === "matter"
    ? `m-${c.document_id}-${c.page}-${c.span[0]}-${i}`
    : `a-${c.source}-${c.version}-${c.article_or_section}-${i}`;
}

const SUGGESTED_PROMPTS = [
  "ما هي مهلة الإخطار القانونية لإنهاء العقد؟",
  "هل تم احترام مسطرة الاستماع المنصوص عليها في المادة 62؟",
  "استخرج التزامات المشغل والأجير من الوثائق المرفوعة",
  "ما هي التعويضات المستحقة في حالة الفصل التعسفي؟",
];

export function Chat({
  matterId,
  onSelectCitation,
}: {
  matterId: number | null;
  onSelectCitation?: (citation: Citation) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop =
        scrollContainerRef.current.scrollHeight;
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, busy]);

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const send = useCallback(
    async (overrideText?: string) => {
      const content = (overrideText ?? input).trim();
      if (!content || busy) return;
      if (matterId === null) {
        setToast("أنشئ أو اختر ملف قضية أولاً للبدء / Veuillez sélectionner un dossier");
        return;
      }
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setInput("");
      setToast(null);
      setBusy(true);
      setMessages((m) => [...m, { role: "user", text: content, citations: [] }]);

      let citations: Citation[] = [];
      let text = "";
      let error: string | undefined;
      let notFound = false;

      const apply = () =>
        setMessages((m) => {
          const next = [...m];
          next[next.length - 1] = {
            role: "assistant",
            text,
            citations,
            notFound,
            error,
          };
          return next;
        });

      setMessages((m) => [
        ...m,
        { role: "assistant", text: "", citations: [] },
      ]);

      const onEvent = (ev: SseEvent) => {
        if (ev.type === "citations") citations = ev.citations;
        else if (ev.type === "token") text += ev.text;
        else if (ev.type === "done") notFound = ev.not_found ?? false;
        else if (ev.type === "error") error = `${ev.code}: ${ev.detail}`;
        apply();
      };

      try {
        for await (const ev of streamChat(matterId, content, {
          signal: controller.signal,
        })) {
          onEvent(ev);
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          error = "تم إيقاف التوليد / Génération arrêtée";
        } else if (e instanceof ApiError) {
          error = `${e.status}: ${e.message}`;
          setToast(`خطأ في الاتصال بالخادم (${e.status}) / Erreur serveur`);
        } else {
          error = e instanceof Error ? e.message : "request failed";
          setToast("تعذر الاتصال بالخادم / Erreur de connexion réseau");
        }
        apply();
      } finally {
        setBusy(false);
      }
    },
    [input, busy, matterId],
  );

  return (
    <section
      aria-label="chat"
      className="flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm"
    >
      {/* Chat Sub-Header */}
      <div className="flex items-center justify-between border-b border-border/70 px-4 py-3 bg-muted/20">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Bot className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
              المساعد القانوني الذكي
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.2 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">
                <ShieldCheck className="h-3 w-3" /> مؤصل قانونياً
              </span>
            </h2>
            <p className="text-[11px] text-muted-foreground">
              {matterId
                ? `مرتبط بالقضية #${matterId} · نصوص القانون المغربي ووثائق الملف`
                : "يرجى اختيار قضية من الأعلى"}
            </p>
          </div>
        </div>

        {messages.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setMessages([])}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5 me-1" /> مسح المحادثة
          </Button>
        )}
      </div>

      {/* Alert Toast */}
      {toast && (
        <div
          role="alert"
          className="mx-4 mt-3 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive font-medium animate-in fade-in"
        >
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{toast}</span>
        </div>
      )}

      {/* Chat Messages Stream */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto p-4 space-y-4"
      >
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center py-8 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary mb-3">
              <Scale className="h-7 w-7" />
            </div>
            <h3 className="text-base font-bold text-foreground">
              مرحباً بك في فصل (FASL)
            </h3>
            <p className="mt-1 max-w-sm text-xs text-muted-foreground leading-relaxed">
              اطرح أي استفسار قانوني حول وثائق ملفك، أو استشر نصوص القانون المغربي (مدونة الشغل، قانون الالتزامات والعقود، مدونة التجارة).
            </p>

            {/* Quick Starter Chips */}
            <div className="mt-6 flex flex-col gap-1.5 w-full max-w-md">
              <span className="text-[11px] font-semibold text-muted-foreground text-start">
                💡 نماذج أسئلة مقترحة:
              </span>
              {SUGGESTED_PROMPTS.map((prompt, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => void send(prompt)}
                  disabled={busy || matterId === null}
                  className="flex items-center justify-between rounded-xl border border-border/80 bg-muted/40 hover:bg-muted p-2.5 text-start text-xs text-foreground transition-colors cursor-pointer disabled:opacity-50"
                >
                  <span>{prompt}</span>
                  <Sparkles className="h-3.5 w-3.5 text-primary shrink-0 ms-2" />
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={cn(
              "flex gap-3 text-sm",
              m.role === "user" ? "flex-row-reverse" : "flex-row",
            )}
          >
            {/* Avatar */}
            <div
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-semibold",
                m.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground border border-border",
              )}
            >
              {m.role === "user" ? (
                <User className="h-4 w-4" />
              ) : (
                <Bot className="h-4 w-4 text-primary" />
              )}
            </div>

            {/* Bubble Container */}
            <div
              className={cn(
                "flex flex-col max-w-[85%]",
                m.role === "user" ? "items-end" : "items-start",
              )}
            >
              <div
                className={cn(
                  "relative rounded-2xl px-4 py-3 leading-relaxed shadow-xs transition-all",
                  m.role === "user"
                    ? "bg-primary text-primary-foreground rounded-te-none"
                    : "bg-muted/70 text-foreground border border-border/80 rounded-ts-none",
                )}
              >
                {/* Content */}
                <div className="whitespace-pre-wrap">
                  {m.text || (m.error ? "" : "جارٍ الصياغة والتحليل القانوني…")}
                </div>

                {/* Error */}
                {m.error && (
                  <div className="mt-2 text-xs text-destructive font-medium">
                    ⚠️ {m.error}
                  </div>
                )}
              </div>

              {/* Actions & Copy */}
              {m.role === "assistant" && m.text && (
                <div className="mt-1 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleCopy(m.text, i)}
                    className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground cursor-pointer px-1 py-0.5 rounded transition-colors"
                  >
                    {copiedIndex === i ? (
                      <>
                        <Check className="h-3 w-3 text-emerald-500" />
                        <span className="text-emerald-500">تم النسخ</span>
                      </>
                    ) : (
                      <>
                        <Copy className="h-3 w-3" />
                        <span>نسخ</span>
                      </>
                    )}
                  </button>
                </div>
              )}

              {/* Grounded Citations Badges */}
              {m.citations.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {m.citations.map((c, j) => (
                    <CitationDomainBadge
                      key={eventKey(c, j)}
                      citation={c}
                      anchorId={`claim-${i}-${j}`}
                      onSelect={() => onSelectCitation?.(c)}
                    />
                  ))}
                </div>
              )}

              {/* Provisional disclaimer if not found */}
              {m.notFound && (
                <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400 font-medium">
                  ⚖️ لم يتم العثور على سند مباشر مطابق — الإجابة استرشادية مؤقتة.
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Input / Control Bar */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        className="border-t border-border/80 p-3 bg-muted/20"
      >
        <div className="relative flex items-center rounded-xl border border-input bg-card shadow-sm transition-all focus-within:ring-2 focus-within:ring-ring focus-within:border-transparent">
          <input
            aria-label="question"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
            placeholder={
              matterId
                ? "اكتب استفسارك القانوني حول القضية… (Enter للإرسال)"
                : "اختر قضية للبدء في طرح الأسئلة…"
            }
            className="flex-1 bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />

          <div className="flex items-center gap-1.5 pe-2">
            {busy ? (
              <Button
                type="button"
                size="sm"
                variant="destructive"
                onClick={() => abortRef.current?.abort()}
                className="h-8 gap-1 text-xs"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                <span>إيقاف</span>
              </Button>
            ) : (
              <Button
                type="submit"
                size="sm"
                disabled={!input.trim() || matterId === null}
                className="h-8 gap-1 text-xs"
              >
                <span>إرسال</span>
                <Send className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>
      </form>
    </section>
  );
}

"use client";

import { useCallback, useRef, useState } from "react";
import {
  ApiError,
  streamChat,
  type Citation,
  type SseEvent,
} from "@/lib/api";
import { CitationDomainBadge } from "./citation-domain-badge";

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

export function Chat({ matterId }: { matterId: number | null }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const scrollToSource = useCallback((anchorId: string) => {
    document
      .querySelector(`[data-claim-source="${anchorId}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const send = useCallback(async () => {
    const content = input.trim();
    if (!content || busy) return;
    if (matterId === null) {
      setToast("أنشئ قضية أولاً / Créez d'abord un dossier");
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
        error = "aborted";
      } else if (e instanceof ApiError) {
        error = `${e.status}: ${e.message}`;
        setToast(`خطأ في الشبكة / Erreur réseau (${e.status})`);
      } else {
        error = e instanceof Error ? e.message : "request failed";
        setToast("خطأ في الشبكة / Erreur réseau");
      }
      apply();
    } finally {
      setBusy(false);
    }
  }, [input, busy, matterId]);

  return (
    <section aria-label="chat" className="flex flex-col gap-3">
      {toast && (
        <div
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
        >
          {toast}
        </div>
      )}
      <div className="flex min-h-64 flex-col gap-3 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
        {messages.length === 0 && (
          <p className="text-sm text-neutral-500">
            اسأل عن قضيتك أو عن القانون — تُعرض الاستشهادات حسب المصدر.
            <br />
            Posez une question sur votre dossier ou sur la loi.
          </p>
        )}
        {messages.map((m, i) => (
          <article
            key={i}
            className={
              m.role === "user" ? "self-end text-end" : "self-start text-start"
            }
          >
            <div
              className={
                m.role === "user"
                  ? "rounded-lg bg-sky-600 px-3 py-2 text-sm text-white"
                  : "rounded-lg bg-neutral-100 px-3 py-2 text-sm dark:bg-neutral-800"
              }
            >
              {m.text || (m.error ? "" : "…")}
              {m.error && <span className="text-red-500">{m.error}</span>}
            </div>
            {m.citations.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {m.citations.map((c, j) => (
                  <CitationDomainBadge
                    key={eventKey(c, j)}
                    citation={c}
                    anchorId={`claim-${i}-${j}`}
                    onSelect={scrollToSource}
                  />
                ))}
              </div>
            )}
            {m.notFound && (
              <p className="mt-1 text-xs text-neutral-500">
                لا توجد مادة مطابقة — إجابة مؤقتة / Réponse provisoire
              </p>
            )}
          </article>
        ))}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          aria-label="question"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="اكتب سؤالك… / Écrivez votre question…"
          className="flex-1 rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm dark:border-neutral-700"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-md bg-sky-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "…" : "إرسال / Envoyer"}
        </button>
        {busy && (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700"
          >
            إيقاف / Stop
          </button>
        )}
      </form>
    </section>
  );
}

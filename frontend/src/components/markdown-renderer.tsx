"use client";

import { useMemo } from "react";
import { Marked, type Tokens } from "marked";
import DOMPurify from "dompurify";
import { cn } from "@/lib/utils";

// Configure marked instance
const markedInstance = new Marked({
  gfm: true,
  breaks: true,
});

// Configure link rendering to safely open external links in new tab
markedInstance.use({
  renderer: {
    link(token: Tokens.Link) {
      const titleAttr = token.title ? ` title="${token.title}"` : "";
      return `<a href="${token.href}" target="_blank" rel="noopener noreferrer"${titleAttr}>${token.text}</a>`;
    },
  },
});

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className }: MarkdownRendererProps) {
  const html = useMemo(() => {
    if (!content) return "";
    try {
      const parsed = markedInstance.parse(content) as string;
      if (typeof window !== "undefined") {
        return DOMPurify.sanitize(parsed, {
          ADD_ATTR: ["target", "rel"],
        });
      }
      return parsed;
    } catch {
      return content;
    }
  }, [content]);

  return (
    <div
      className={cn("markdown-content text-sm leading-relaxed", className)}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

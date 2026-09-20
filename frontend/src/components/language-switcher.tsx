"use client";

import * as React from "react";
import { Globe, Check, ChevronDown } from "lucide-react";
import { useI18n, SUPPORTED_LANGUAGES, type Language } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

interface LanguageSwitcherProps {
  variant?: "dropdown" | "select" | "toggle" | "button";
  className?: string;
  showLabel?: boolean;
}

export function LanguageSwitcher({
  variant = "dropdown",
  className,
  showLabel = true,
}: LanguageSwitcherProps) {
  const { language, setLanguage, t } = useI18n();

  const current =
    SUPPORTED_LANGUAGES.find((l) => l.code === language) ??
    SUPPORTED_LANGUAGES[0];

  // 1. Button Group / Toggle Mode
  if (variant === "toggle") {
    return (
      <div
        role="group"
        aria-label={t.nav.changeLanguage}
        className={cn(
          "inline-flex items-center rounded-lg border border-border bg-muted/40 p-0.5 text-xs font-semibold shadow-xs",
          className,
        )}
      >
        {SUPPORTED_LANGUAGES.map((lang) => {
          const isActive = lang.code === language;
          return (
            <button
              key={lang.code}
              type="button"
              onClick={() => setLanguage(lang.code)}
              aria-pressed={isActive}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-all cursor-pointer",
                isActive
                  ? "bg-card text-foreground font-bold shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <span>{lang.flag}</span>
              <span>{lang.nativeLabel}</span>
            </button>
          );
        })}
      </div>
    );
  }

  // 2. Select Mode
  if (variant === "select") {
    return (
      <div className={cn("w-36", className)}>
        <Select
          value={language}
          onValueChange={(val) => setLanguage(val as Language)}
        >
          <SelectTrigger className="h-9 bg-card text-xs">
            <div className="flex items-center gap-1.5">
              <Globe className="h-3.5 w-3.5 text-primary" />
              <SelectValue>{current.nativeLabel}</SelectValue>
            </div>
          </SelectTrigger>
          <SelectContent>
            {SUPPORTED_LANGUAGES.map((lang) => (
              <SelectItem key={lang.code} value={lang.code} className="text-xs">
                <div className="flex items-center gap-2">
                  <span>{lang.flag}</span>
                  <span className="font-medium">{lang.nativeLabel}</span>
                  <span className="text-muted-foreground text-[10px]">
                    ({lang.label})
                  </span>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }

  // 3. Dropdown Menu Mode (Default & Button)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn(
            "h-9 gap-1.5 text-xs font-semibold text-foreground bg-muted/40 hover:bg-muted border-border/80 shadow-xs",
            className,
          )}
          aria-label={t.nav.changeLanguage}
        >
          <Globe className="h-3.5 w-3.5 text-primary shrink-0" />
          <span className="hidden sm:inline">{current.nativeLabel}</span>
          <span className="sm:hidden uppercase font-mono">{current.code}</span>
          <ChevronDown className="h-3 w-3 opacity-60 ms-0.5" />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuLabel className="text-[11px] text-muted-foreground flex items-center gap-1.5 font-medium">
          <Globe className="h-3.5 w-3.5 text-primary" />
          <span>{t.nav.changeLanguage}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {SUPPORTED_LANGUAGES.map((lang) => {
          const isSelected = lang.code === language;
          return (
            <DropdownMenuItem
              key={lang.code}
              onClick={() => setLanguage(lang.code)}
              className={cn(
                "flex items-center justify-between text-xs cursor-pointer py-2",
                isSelected && "bg-muted font-bold text-primary",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm">{lang.flag}</span>
                <span className="font-medium">{lang.nativeLabel}</span>
                <span className="text-[10px] text-muted-foreground">
                  ({lang.label})
                </span>
              </div>
              {isSelected && <Check className="h-3.5 w-3.5 text-primary shrink-0" />}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

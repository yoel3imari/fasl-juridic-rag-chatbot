"use client";

import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  listChatModels,
  getLlmSettings,
  saveLlmSettings,
  type ModelsResponse,
  type ProviderOption,
  type ModelOption,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  Cpu,
  ShieldCheck,
  Cloud,
  Sparkles,
  Check,
  AlertTriangle,
  AlertCircle,
  Zap,
  Layers,
  Settings2,
  KeyRound,
  Eye,
  EyeOff,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

const CLOUD_KEY_PROVIDERS = ["openrouter", "openai", "anthropic", "google", "groq"];

// Fallback providers in case backend is unreachable during render
const FALLBACK_PROVIDERS: ProviderOption[] = [
  {
    id: "ollama",
    name: "Ollama (Local)",
    type: "local",
    is_external: false,
    description: "Local on-device inference with zero data transmission. Strict privacy compliant.",
    default_model: "llama3.2",
    models: [
      { id: "llama3.2", name: "Llama 3.2 (3B)", description: "Lightweight, fast, local privacy", recommended: true },
      { id: "llama3.1", name: "Llama 3.1 (8B)", description: "Balanced general reasoning" },
      { id: "qwen2.5", name: "Qwen 2.5 (7B/14B)", description: "Excellent Arabic & multilingual legal understanding", recommended: true },
      { id: "mistral", name: "Mistral (7B)", description: "Concise legal summarization" },
      { id: "deepseek-r1", name: "DeepSeek R1 Distill", description: "Deep step-by-step reasoning" },
    ],
  },
  {
    id: "openrouter",
    name: "OpenRouter (Unified Cloud)",
    type: "cloud",
    is_external: true,
    description: "Access dozens of state-of-the-art models via OpenRouter unified gateway.",
    default_model: "anthropic/claude-3.5-sonnet",
    models: [
      { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet", description: "Top benchmark for legal reasoning and drafting", recommended: true },
      { id: "openai/gpt-4o", name: "GPT-4o", description: "High-capability omnimodel" },
      { id: "google/gemini-2.0-flash", name: "Gemini 2.0 Flash", description: "Ultra fast with large context window", recommended: true },
      { id: "meta-llama/llama-3.3-70b-instruct", name: "Llama 3.3 70B Instruct", description: "High performance open-weights" },
      { id: "deepseek/deepseek-r1", name: "DeepSeek R1", description: "Frontier reasoning for complex statutory disputes" },
    ],
  },
  {
    id: "openai",
    name: "OpenAI",
    type: "cloud",
    is_external: true,
    description: "Direct OpenAI API connection (requires OPENAI_API_KEY).",
    default_model: "gpt-4o",
    models: [
      { id: "gpt-4o", name: "GPT-4o", description: "Flagship intelligent model", recommended: true },
      { id: "gpt-4o-mini", name: "GPT-4o Mini", description: "Fast, cost-efficient analysis" },
      { id: "o1-mini", name: "o1-mini", description: "Advanced reasoning for complex statutory analysis" },
    ],
  },
  {
    id: "anthropic",
    name: "Anthropic",
    type: "cloud",
    is_external: true,
    description: "Direct Anthropic Claude API connection (requires ANTHROPIC_API_KEY).",
    default_model: "claude-3-5-sonnet-latest",
    models: [
      { id: "claude-3-5-sonnet-latest", name: "Claude 3.5 Sonnet", description: "State-of-the-art legal precision", recommended: true },
      { id: "claude-3-5-haiku-latest", name: "Claude 3.5 Haiku", description: "Fast and concise responses" },
    ],
  },
  {
    id: "google",
    name: "Google Gemini",
    type: "cloud",
    is_external: true,
    description: "Direct Google Gemini API connection (requires GEMINI_API_KEY).",
    default_model: "gemini-2.0-flash",
    models: [
      { id: "gemini-2.0-flash", name: "Gemini 2.0 Flash", description: "High-speed next-gen model", recommended: true },
      { id: "gemini-1.5-pro", name: "Gemini 1.5 Pro", description: "Deep document analysis & massive context" },
    ],
  },
  {
    id: "groq",
    name: "Groq",
    type: "cloud",
    is_external: true,
    description: "Ultra-low latency LPU cloud inference (requires GROQ_API_KEY).",
    default_model: "llama-3.3-70b-versatile",
    models: [
      { id: "llama-3.3-70b-versatile", name: "Llama 3.3 70B (Groq)", description: "Near-instant token generation", recommended: true },
      { id: "mixtral-8x7b-32768", name: "Mixtral 8x7B (Groq)", description: "Fast mixture of experts" },
    ],
  },
];

export function ModelSwitcherModal({
  open,
  onOpenChange,
  currentProvider,
  currentModel,
  onSelectModel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentProvider: string;
  currentModel: string;
  onSelectModel: (provider: string, model: string) => void;
}) {
  const { t } = useI18n();
  const [providers, setProviders] = React.useState<ProviderOption[]>(FALLBACK_PROVIDERS);
  const [selectedProviderId, setSelectedProviderId] = React.useState(currentProvider);
  const [selectedModelId, setSelectedModelId] = React.useState(currentModel);
  const [customModelInput, setCustomModelInput] = React.useState("");
  const [isCustom, setIsCustom] = React.useState(false);
  const [keysStatus, setKeysStatus] = React.useState<Record<string, boolean>>({});
  const [maskedKeys, setMaskedKeys] = React.useState<Record<string, string | null>>({});
  const [keyInputs, setKeyInputs] = React.useState<Record<string, string>>({});
  const [touchedKeys, setTouchedKeys] = React.useState<Record<string, boolean>>({});
  const [showKey, setShowKey] = React.useState<Record<string, boolean>>({});
  const [settingsAvailable, setSettingsAvailable] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setSelectedProviderId(currentProvider);
      setSelectedModelId(currentModel);
      setIsCustom(false);
      setCustomModelInput("");
      setKeyInputs({});
      setTouchedKeys({});
      setShowKey({});
      setSaveError(null);
      setSaving(false);

      listChatModels()
        .then((res) => {
          if (res.providers && res.providers.length > 0) {
            setProviders(res.providers);
          }
        })
        .catch(() => {
          // Use fallback
        });

      getLlmSettings()
        .then((res) => {
          setKeysStatus(res.keys_status ?? {});
          setMaskedKeys(res.masked_keys ?? {});
          setSettingsAvailable(true);
        })
        .catch(() => {
          // Backend not ready (e.g. 404) — fall back to localStorage-only
          setKeysStatus({});
          setMaskedKeys({});
          setSettingsAvailable(false);
        });
    }
  }, [open, currentProvider, currentModel]);

  const activeProvider =
    providers.find((p) => p.id === selectedProviderId) ?? providers[0];

  const handleProviderSelect = (providerId: string) => {
    setSelectedProviderId(providerId);
    const p = providers.find((item) => item.id === providerId);
    if (p) {
      setSelectedModelId(p.default_model);
      setIsCustom(false);
    }
  };

  const handleModelSelect = (modelId: string) => {
    setSelectedModelId(modelId);
    setIsCustom(false);
  };

  const handleApply = () => {
    const finalModel = isCustom ? customModelInput.trim() : selectedModelId;
    if (!finalModel || saving) return;
    if (!settingsAvailable) {
      onSelectModel(selectedProviderId, finalModel);
      onOpenChange(false);
      return;
    }
    const apiKeys: Record<string, string | null> = {};
    for (const pid of CLOUD_KEY_PROVIDERS) {
      if (!touchedKeys[pid]) continue;
      const raw = (keyInputs[pid] ?? "").trim();
      if (raw) {
        apiKeys[pid] = raw;
      } else if (keysStatus[pid]) {
        apiKeys[pid] = null;
      }
    }
    setSaving(true);
    setSaveError(null);
    saveLlmSettings({
      provider: selectedProviderId,
      model: finalModel,
      ...(Object.keys(apiKeys).length > 0 ? { api_keys: apiKeys } : {}),
    })
      .then(() => {
        onSelectModel(selectedProviderId, finalModel);
        onOpenChange(false);
      })
      .catch(() => {
        setSaveError(t.modelSwitcher.saveError);
      })
      .finally(() => {
        setSaving(false);
      });
  };

  const cloudProviders = providers.filter((p) =>
    CLOUD_KEY_PROVIDERS.includes(p.id),
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl sm:max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2 text-primary font-semibold text-xs uppercase tracking-wider">
            <Cpu className="h-4 w-4" />
            <span>{t.modelSwitcher.modalBadge}</span>
          </div>
          <DialogTitle className="text-lg sm:text-xl font-bold tracking-tight">
            {t.modelSwitcher.modalTitle}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground leading-relaxed">
            {t.modelSwitcher.modalDesc}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 pt-2">
          {/* Privacy Banner */}
          {activeProvider.type === "local" ? (
            <div className="flex items-start gap-2.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-800 dark:text-emerald-200">
              <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" />
              <div className="space-y-0.5">
                <span className="font-semibold">{t.modelSwitcher.localProviderBadge}</span>
                <p className="text-[11px] text-emerald-700 dark:text-emerald-300 leading-relaxed">
                  {t.modelSwitcher.strictPrivacyNote}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-900 dark:text-amber-200">
              <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <div className="space-y-0.5">
                <span className="font-semibold">{t.modelSwitcher.cloudProviderBadge}</span>
                <p className="text-[11px] text-amber-700 dark:text-amber-300 leading-relaxed">
                  {t.modelSwitcher.cloudConsentNote}
                </p>
              </div>
            </div>
          )}

          {/* Provider Selection */}
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Layers className="h-3.5 w-3.5 text-primary" />
              <span>{t.modelSwitcher.providerSectionTitle}</span>
            </h4>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {providers.map((p) => {
                const isSelected = p.id === selectedProviderId;
                const isLocal = p.type === "local";

                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => handleProviderSelect(p.id)}
                    className={cn(
                      "flex flex-col items-start p-3 rounded-xl border text-start transition-all cursor-pointer shadow-2xs relative",
                      isSelected
                        ? "border-primary bg-primary/10 ring-1 ring-primary/40 shadow-xs"
                        : "border-border/70 bg-background/50 hover:bg-muted/40 hover:border-border",
                    )}
                  >
                    <div className="flex items-center justify-between w-full mb-1.5">
                      <span className="font-semibold text-xs text-foreground truncate">
                        {p.name}
                      </span>
                      {isSelected && (
                        <div className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                          <Check className="h-2.5 w-2.5 stroke-[3]" />
                        </div>
                      )}
                    </div>

                    <div className="flex items-center gap-1">
                      {isLocal ? (
                        <Badge
                          variant="outline"
                          className="text-[9px] px-1.5 py-0 border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium"
                        >
                          <ShieldCheck className="h-2.5 w-2.5 me-0.5" />
                          Local
                        </Badge>
                      ) : (
                        <Badge
                          variant="outline"
                          className="text-[9px] px-1.5 py-0 border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400 font-medium"
                        >
                          <Cloud className="h-2.5 w-2.5 me-0.5" />
                          Cloud
                        </Badge>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Model Selection */}
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
              <span>{t.modelSwitcher.modelSectionTitle}</span>
            </h4>

            <div className="space-y-2">
              {activeProvider.models.map((m) => {
                const isSelected = !isCustom && m.id === selectedModelId;

                return (
                  <div
                    key={m.id}
                    onClick={() => handleModelSelect(m.id)}
                    className={cn(
                      "flex items-center justify-between p-3 rounded-xl border transition-all cursor-pointer shadow-2xs",
                      isSelected
                        ? "border-primary bg-card ring-1 ring-primary/40 shadow-xs"
                        : "border-border/70 bg-background/40 hover:bg-muted/40 hover:border-border",
                    )}
                  >
                    <div className="min-w-0 flex-1 me-3">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold text-foreground">
                          {m.name}
                        </span>
                        <code className="text-[10px] font-mono text-muted-foreground bg-muted/60 px-1.5 py-0.2 rounded">
                          {m.id}
                        </code>
                        {m.recommended && (
                          <Badge
                            variant="secondary"
                            className="text-[9px] px-1.5 py-0 bg-primary/15 text-primary border-primary/20 font-medium"
                          >
                            {t.modelSwitcher.recommendedBadge}
                          </Badge>
                        )}
                      </div>
                      {m.description && (
                        <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
                          {m.description}
                        </p>
                      )}
                    </div>

                    <div className="shrink-0">
                      {isSelected ? (
                        <div className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-2xs">
                          <Check className="h-3 w-3 stroke-[3]" />
                        </div>
                      ) : (
                        <div className="h-4 w-4 rounded-full border border-border/70" />
                      )}
                    </div>
                  </div>
                );
              })}

              {/* Custom Model Option */}
              <div
                onClick={() => setIsCustom(true)}
                className={cn(
                  "p-3 rounded-xl border transition-all cursor-pointer shadow-2xs space-y-2",
                  isCustom
                    ? "border-primary bg-card ring-1 ring-primary/40 shadow-xs"
                    : "border-dashed border-border/70 bg-background/30 hover:bg-muted/30",
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Settings2 className="h-3.5 w-3.5 text-primary" />
                    <span className="text-xs font-semibold text-foreground">
                      {t.modelSwitcher.customModelOption}
                    </span>
                  </div>
                  {isCustom ? (
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-2xs">
                      <Check className="h-3 w-3 stroke-[3]" />
                    </div>
                  ) : (
                    <div className="h-4 w-4 rounded-full border border-border/70" />
                  )}
                </div>

                {isCustom && (
                  <div className="pt-1 animate-in fade-in space-y-1">
                    <Input
                      autoFocus
                      value={customModelInput}
                      onChange={(e) => setCustomModelInput(e.target.value)}
                      placeholder={t.modelSwitcher.customModelPlaceholder}
                      className="text-xs h-9 bg-background/90 font-mono"
                    />
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* API Keys (cloud providers only) */}
          {settingsAvailable && cloudProviders.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-primary" />
                <span>3. API Keys</span>
              </h4>

              <div className="space-y-2">
                {cloudProviders.map((p) => {
                  const masked = maskedKeys[p.id] ?? null;
                  const hasKey = keysStatus[p.id] ?? false;
                  const visible = showKey[p.id] ?? false;
                  const placeholder =
                    hasKey && masked
                      ? `${t.modelSwitcher.apiKeySavedPrefix} ${masked}`
                      : t.modelSwitcher.apiKeyPlaceholder;
                  return (
                    <div
                      key={p.id}
                      className="p-3 rounded-xl border border-border/70 bg-background/40 space-y-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <label
                          htmlFor={`api-key-${p.id}`}
                          className="text-xs font-semibold text-foreground flex items-center gap-1.5 min-w-0"
                        >
                          <KeyRound className="h-3.5 w-3.5 text-primary shrink-0" />
                          <span className="truncate">
                            {t.modelSwitcher.apiKeyLabel.replace("{provider}", p.name)}
                          </span>
                        </label>
                        {hasKey ? (
                          <Badge
                            variant="outline"
                            className="text-[9px] px-1.5 py-0 border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium shrink-0"
                          >
                            <Check className="h-2.5 w-2.5 me-0.5" />
                            {t.modelSwitcher.apiKeySavedPrefix}
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="text-[9px] px-1.5 py-0 border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium shrink-0"
                          >
                            <AlertTriangle className="h-2.5 w-2.5 me-0.5" />
                            {t.modelSwitcher.apiKeyMissing}
                          </Badge>
                        )}
                      </div>
                      <div className="relative">
                        <input
                          id={`api-key-${p.id}`}
                          type={visible ? "text" : "password"}
                          value={keyInputs[p.id] ?? ""}
                          onChange={(e) => {
                            const v = e.target.value;
                            setKeyInputs((prev) => ({ ...prev, [p.id]: v }));
                            setTouchedKeys((prev) => ({ ...prev, [p.id]: true }));
                            setSaveError(null);
                          }}
                          placeholder={placeholder}
                          autoComplete="off"
                          spellCheck={false}
                          className="flex h-9 w-full rounded-md border border-input bg-background/90 pe-10 ps-3 text-xs font-mono shadow-2xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        />
                        <button
                          type="button"
                          onClick={() =>
                            setShowKey((prev) => ({ ...prev, [p.id]: !visible }))
                          }
                          title={
                            visible
                              ? t.modelSwitcher.apiKeyHide
                              : t.modelSwitcher.apiKeyShow
                          }
                          aria-label={
                            visible
                              ? t.modelSwitcher.apiKeyHide
                              : t.modelSwitcher.apiKeyShow
                          }
                          className="absolute end-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                        >
                          {visible ? (
                            <EyeOff className="h-3.5 w-3.5" />
                          ) : (
                            <Eye className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {saveError && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive font-medium"
          >
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{saveError}</span>
          </div>
        )}

        <DialogFooter className="gap-2 pt-3 sm:gap-0">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            className="text-xs h-9 rounded-lg"
          >
            {t.modelSwitcher.cancelButton}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={handleApply}
            disabled={(isCustom && !customModelInput.trim()) || saving}
            className="text-xs h-9 rounded-lg gap-1.5 font-medium shadow-2xs"
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Zap className="h-3.5 w-3.5" />
            )}
            <span>{saving ? t.modelSwitcher.saving : t.modelSwitcher.saveButton}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

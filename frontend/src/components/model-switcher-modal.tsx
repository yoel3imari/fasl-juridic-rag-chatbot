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
  testLlmConnection,
  type ProviderOption,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  Cpu,
  ShieldCheck,
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
  Globe,
  Activity,
} from "lucide-react";
import { cn } from "@/lib/utils";

const CLOUD_KEY_PROVIDERS = ["openrouter", "openai", "anthropic", "google", "groq"];

// Fallback providers in case backend is unreachable during initial render
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
    id: "groq",
    name: "Groq (Fast Cloud)",
    type: "cloud",
    is_external: true,
    description: "Ultra-low latency cloud inference via standard OpenAI-compatible protocol.",
    default_model: "llama-3.3-70b-versatile",
    models: [
      { id: "llama-3.3-70b-versatile", name: "Llama 3.3 70B (Groq)", description: "Near-instant token generation", recommended: true },
      { id: "mixtral-8x7b-32768", name: "Mixtral 8x7B (Groq)", description: "Fast mixture of experts" },
    ],
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    type: "cloud",
    is_external: true,
    description: "Unified gateway to state-of-the-art models (Claude, GPT-4o, Gemini, DeepSeek).",
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
  const [baseUrls, setBaseUrls] = React.useState<Record<string, string | null>>({});
  const [keyInputs, setKeyInputs] = React.useState<Record<string, string>>({});
  const [baseUrlInputs, setBaseUrlInputs] = React.useState<Record<string, string>>({});
  const [touchedKeys, setTouchedKeys] = React.useState<Record<string, boolean>>({});
  const [showKey, setShowKey] = React.useState(false);
  const [settingsAvailable, setSettingsAvailable] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<{ success: boolean; message: string } | null>(null);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setSelectedProviderId(currentProvider);
      setSelectedModelId(currentModel);
      setIsCustom(false);
      setCustomModelInput("");
      setKeyInputs({});
      setBaseUrlInputs({});
      setTouchedKeys({});
      setShowKey(false);
      setSaveError(null);
      setTestResult(null);
      setSaving(false);
      setTesting(false);

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
          setBaseUrls(res.base_urls ?? {});
          setSettingsAvailable(true);
        })
        .catch(() => {
          setKeysStatus({});
          setMaskedKeys({});
          setBaseUrls({});
          setSettingsAvailable(false);
        });
    }
  }, [open, currentProvider, currentModel]);

  const activeProvider =
    providers.find((p) => p.id === selectedProviderId) ?? providers[0];

  const handleProviderSelect = (providerId: string) => {
    setSelectedProviderId(providerId);
    setShowKey(false);
    setTestResult(null);
    const p = providers.find((item) => item.id === providerId);
    if (p) {
      setSelectedModelId(p.default_model);
      setIsCustom(false);
    }
  };

  const handleModelSelect = (modelId: string) => {
    setSelectedModelId(modelId);
    setIsCustom(false);
    setTestResult(null);
  };

  const handleTestConnection = async () => {
    const finalModel = isCustom ? customModelInput.trim() : selectedModelId;
    if (!finalModel || testing) return;

    setTesting(true);
    setTestResult(null);

    const activeKey = (keyInputs[selectedProviderId] ?? "").trim();
    const activeBaseUrl = (baseUrlInputs[selectedProviderId] ?? "").trim();

    try {
      const result = await testLlmConnection({
        provider: selectedProviderId,
        model: finalModel,
        ...(activeKey ? { api_key: activeKey } : {}),
        ...(activeBaseUrl ? { base_url: activeBaseUrl } : {}),
      });
      setTestResult(result);
    } catch (err: any) {
      setTestResult({
        success: false,
        message: err.message || "Failed to reach backend test endpoint",
      });
    } finally {
      setTesting(false);
    }
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

    const customBaseUrls: Record<string, string | null> = {};
    for (const [pid, url] of Object.entries(baseUrlInputs)) {
      const trimmed = url.trim();
      customBaseUrls[pid] = trimmed || null;
    }

    setSaving(true);
    setSaveError(null);
    saveLlmSettings({
      provider: selectedProviderId,
      model: finalModel,
      ...(Object.keys(apiKeys).length > 0 ? { api_keys: apiKeys } : {}),
      ...(Object.keys(customBaseUrls).length > 0 ? { base_urls: customBaseUrls } : {}),
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

  const isLocal = activeProvider.type === "local";
  const activeKeyMasked = maskedKeys[activeProvider.id] ?? null;
  const activeHasKey = keysStatus[activeProvider.id] ?? false;
  const activeKeyInput = keyInputs[activeProvider.id] ?? "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader className="pb-1">
          <div className="flex items-center gap-2 text-primary font-semibold text-xs uppercase tracking-wider">
            <Cpu className="h-4 w-4" />
            <span>{t.modelSwitcher.modalBadge}</span>
          </div>
          <DialogTitle className="text-lg font-bold tracking-tight">
            {t.modelSwitcher.modalTitle}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground leading-relaxed">
            {t.modelSwitcher.modalDesc}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-1">
          {/* Provider Selection Tabs */}
          <div className="space-y-1.5">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Layers className="h-3.5 w-3.5 text-primary" />
              <span>{t.modelSwitcher.providerSectionTitle}</span>
            </h4>

            <div className="flex flex-wrap gap-1.5 p-1 rounded-xl bg-muted/40 border border-border/60">
              {providers.map((p) => {
                const isSelected = p.id === selectedProviderId;
                const pLocal = p.type === "local";

                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => handleProviderSelect(p.id)}
                    className={cn(
                      "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all cursor-pointer",
                      isSelected
                        ? "bg-background text-foreground shadow-xs ring-1 ring-border font-semibold"
                        : "text-muted-foreground hover:text-foreground hover:bg-background/50",
                    )}
                  >
                    <span>{p.name}</span>
                    {pLocal ? (
                      <span className="inline-flex items-center px-1.5 py-0.2 rounded-full text-[9px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-semibold">
                        Local
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-1.5 py-0.2 rounded-full text-[9px] bg-sky-500/15 text-sky-600 dark:text-sky-400 font-semibold">
                        Cloud
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Contextual Provider Details & Credentials */}
          <div className="p-3 rounded-xl border border-border/70 bg-card/50 space-y-2.5">
            {/* Privacy note */}
            {isLocal ? (
              <div className="flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-300">
                <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
                <span className="text-[11px] leading-relaxed">
                  {t.modelSwitcher.strictPrivacyNote}
                </span>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-xs text-amber-800 dark:text-amber-300">
                <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
                <span className="text-[11px] leading-relaxed">
                  {t.modelSwitcher.cloudConsentNote}
                </span>
              </div>
            )}

            {/* Contextual API Key & Base URL (Cloud only) */}
            {!isLocal && settingsAvailable && (
              <div className="pt-2 border-t border-border/50 space-y-2">
                {/* API Key */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <label
                      htmlFor={`api-key-${activeProvider.id}`}
                      className="text-xs font-semibold text-foreground flex items-center gap-1.5"
                    >
                      <KeyRound className="h-3.5 w-3.5 text-primary shrink-0" />
                      <span>
                        {t.modelSwitcher.apiKeyLabel.replace("{provider}", activeProvider.name)}
                      </span>
                    </label>
                    {activeHasKey ? (
                      <Badge
                        variant="outline"
                        className="text-[9px] px-1.5 py-0 border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium shrink-0"
                      >
                        <Check className="h-2.5 w-2.5 me-0.5" />
                        {activeKeyMasked ? `${t.modelSwitcher.apiKeySavedPrefix} ${activeKeyMasked}` : t.modelSwitcher.apiKeySavedPrefix}
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
                      id={`api-key-${activeProvider.id}`}
                      type={showKey ? "text" : "password"}
                      value={activeKeyInput}
                      onChange={(e) => {
                        const v = e.target.value;
                        setKeyInputs((prev) => ({ ...prev, [activeProvider.id]: v }));
                        setTouchedKeys((prev) => ({ ...prev, [activeProvider.id]: true }));
                        setSaveError(null);
                        setTestResult(null);
                      }}
                      placeholder={
                        activeHasKey && activeKeyMasked
                          ? `${t.modelSwitcher.apiKeySavedPrefix} ${activeKeyMasked}`
                          : t.modelSwitcher.apiKeyPlaceholder
                      }
                      autoComplete="off"
                      spellCheck={false}
                      className="flex h-8.5 w-full rounded-lg border border-input bg-background pe-9 ps-3 text-xs font-mono shadow-2xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    />
                    <button
                      type="button"
                      onClick={() => setShowKey((prev) => !prev)}
                      title={showKey ? t.modelSwitcher.apiKeyHide : t.modelSwitcher.apiKeyShow}
                      aria-label={showKey ? t.modelSwitcher.apiKeyHide : t.modelSwitcher.apiKeyShow}
                      className="absolute end-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                    >
                      {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>

                {/* Base URL (Optional / Custom) */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <label
                      htmlFor={`base-url-${activeProvider.id}`}
                      className="text-[11px] font-medium text-muted-foreground flex items-center gap-1.5"
                    >
                      <Globe className="h-3 w-3 text-muted-foreground shrink-0" />
                      <span>Base URL (OpenAI-compatible)</span>
                    </label>
                  </div>
                  <input
                    id={`base-url-${activeProvider.id}`}
                    type="text"
                    value={baseUrlInputs[activeProvider.id] ?? baseUrls[activeProvider.id] ?? ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      setBaseUrlInputs((prev) => ({ ...prev, [activeProvider.id]: v }));
                      setSaveError(null);
                      setTestResult(null);
                    }}
                    placeholder={
                      activeProvider.id === "groq"
                        ? "https://api.groq.com/openai/v1"
                        : activeProvider.id === "openrouter"
                          ? "https://openrouter.ai/api/v1"
                          : activeProvider.id === "openai"
                            ? "https://api.openai.com/v1"
                            : "https://api.example.com/v1"
                    }
                    className="flex h-8 w-full rounded-lg border border-input bg-background/70 px-3 text-xs font-mono text-muted-foreground focus:text-foreground shadow-2xs placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Model Selection */}
          <div className="space-y-1.5">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
              <span>{t.modelSwitcher.modelSectionTitle}</span>
            </h4>

            <div className="space-y-1.5">
              {activeProvider.models.map((m) => {
                const isSelected = !isCustom && m.id === selectedModelId;

                return (
                  <div
                    key={m.id}
                    onClick={() => handleModelSelect(m.id)}
                    className={cn(
                      "flex items-center justify-between p-2.5 rounded-xl border transition-all cursor-pointer shadow-2xs",
                      isSelected
                        ? "border-primary bg-primary/5 ring-1 ring-primary/30"
                        : "border-border/70 bg-background/50 hover:bg-muted/40",
                    )}
                  >
                    <div className="min-w-0 flex-1 me-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-foreground">
                          {m.name}
                        </span>
                        <code className="text-[10px] font-mono text-muted-foreground bg-muted px-1.5 py-0.2 rounded">
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
                        <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
                          {m.description}
                        </p>
                      )}
                    </div>

                    <div className="shrink-0">
                      {isSelected ? (
                        <div className="flex h-4.5 w-4.5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-2xs">
                          <Check className="h-2.5 w-2.5 stroke-[3]" />
                        </div>
                      ) : (
                        <div className="h-3.5 w-3.5 rounded-full border border-border/70" />
                      )}
                    </div>
                  </div>
                );
              })}

              {/* Custom Model Option */}
              <div
                onClick={() => setIsCustom(true)}
                className={cn(
                  "p-2.5 rounded-xl border transition-all cursor-pointer shadow-2xs space-y-1.5",
                  isCustom
                    ? "border-primary bg-primary/5 ring-1 ring-primary/30"
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
                    <div className="flex h-4.5 w-4.5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-2xs">
                      <Check className="h-2.5 w-2.5 stroke-[3]" />
                    </div>
                  ) : (
                    <div className="h-3.5 w-3.5 rounded-full border border-border/70" />
                  )}
                </div>

                {isCustom && (
                  <div className="pt-0.5 space-y-1">
                    <Input
                      autoFocus
                      value={customModelInput}
                      onChange={(e) => {
                        setCustomModelInput(e.target.value);
                        setTestResult(null);
                      }}
                      placeholder={t.modelSwitcher.customModelPlaceholder}
                      className="text-xs h-8.5 bg-background font-mono"
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Connection Test Result Feedback */}
        {testResult && (
          <div
            className={cn(
              "flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-medium animate-in fade-in",
              testResult.success
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200"
                : "border-destructive/30 bg-destructive/10 text-destructive",
            )}
          >
            {testResult.success ? (
              <Check className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
            )}
            <span className="text-[11px] leading-relaxed">{testResult.message}</span>
          </div>
        )}

        {saveError && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive font-medium"
          >
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{saveError}</span>
          </div>
        )}

        <DialogFooter className="flex flex-col-reverse sm:flex-row items-center justify-between gap-2 pt-2 sm:gap-0">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={handleTestConnection}
            disabled={testing || (isCustom && !customModelInput.trim())}
            className="text-xs h-8.5 rounded-lg gap-1.5 w-full sm:w-auto"
          >
            {testing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Activity className="h-3.5 w-3.5 text-primary" />
            )}
            <span>{testing ? "Testing..." : "Test Connection"}</span>
          </Button>

          <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="text-xs h-8.5 rounded-lg"
            >
              {t.modelSwitcher.cancelButton}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleApply}
              disabled={(isCustom && !customModelInput.trim()) || saving}
              className="text-xs h-8.5 rounded-lg gap-1.5 font-medium shadow-2xs"
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Zap className="h-3.5 w-3.5" />
              )}
              <span>{saving ? t.modelSwitcher.saving : t.modelSwitcher.saveButton}</span>
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

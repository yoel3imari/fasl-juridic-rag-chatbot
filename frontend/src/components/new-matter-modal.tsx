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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createMatter, type Matter } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { FolderPlus, MapPin, Briefcase, Globe } from "lucide-react";

const JURISDICTIONS = [
  { value: "casablanca", label: "الدار البيضاء / Casablanca" },
  { value: "rabat", label: "الرباط / Rabat" },
  { value: "tanger", label: "طنجة / Tanger" },
  { value: "marrakech", label: "مراكش / Marrakech" },
  { value: "fes", label: "فاس / Fès" },
  { value: "agadir", label: "أكادير / Agadir" },
  { value: "oujda", label: "وجدة / Oujda" },
];

const MATTER_TYPES = [
  { value: "labor", label: "نزاع شغل / Droit du travail" },
  { value: "commercial", label: "قانون تجاري وشراكات / Droit commercial" },
  { value: "civil", label: "مدني وعقود / Droit civil & Contrats" },
  { value: "administrative", label: "إداري وصفقات / Droit administratif" },
  { value: "general", label: "عام / Général" },
];

const LANGUAGES = [
  { value: "ar", label: "العربية (Arabic)" },
  { value: "fr", label: "Français (French)" },
  { value: "en", label: "English (Anglais)" },
];

export function NewMatterModal({
  open,
  onOpenChange,
  onMatterCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMatterCreated: (matter: Matter) => void;
}) {
  const { t, language: currentAppLanguage } = useI18n();
  const [title, setTitle] = React.useState("");
  const [jurisdiction, setJurisdiction] = React.useState("casablanca");
  const [matterType, setMatterType] = React.useState("labor");
  const [language, setLanguage] = React.useState(currentAppLanguage);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    setLanguage(currentAppLanguage);
  }, [currentAppLanguage]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createMatter({
        title: title.trim(),
        jurisdiction,
        matter_type: matterType,
        language,
      });
      onMatterCreated(created);
      onOpenChange(false);
      setTitle("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Matter creation failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2 text-primary font-semibold text-base">
            <FolderPlus className="h-5 w-5" />
            <span>{t.newMatterModal.modalBadge}</span>
          </div>
          <DialogTitle className="text-xl font-bold">
            {t.newMatterModal.modalTitle}
          </DialogTitle>
          <DialogDescription>
            {t.newMatterModal.modalDesc}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          {error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive font-medium">
              {error}
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-foreground">
              {t.newMatterModal.titleLabel}
            </label>
            <Input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t.newMatterModal.titlePlaceholder}
              className="text-sm"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <Briefcase className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.newMatterModal.matterTypeLabel}</span>
              </label>
              <Select value={matterType} onValueChange={setMatterType}>
                <SelectTrigger>
                  <SelectValue placeholder="اختر نوع النزاع" />
                </SelectTrigger>
                <SelectContent>
                  {MATTER_TYPES.map((type) => (
                    <SelectItem key={type.value} value={type.value}>
                      {type.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <MapPin className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t.newMatterModal.jurisdictionLabel}</span>
              </label>
              <Select value={jurisdiction} onValueChange={setJurisdiction}>
                <SelectTrigger>
                  <SelectValue placeholder="اختر الدائرة" />
                </SelectTrigger>
                <SelectContent>
                  {JURISDICTIONS.map((j) => (
                    <SelectItem key={j.value} value={j.value}>
                      {j.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-foreground flex items-center gap-1">
              <Globe className="h-3.5 w-3.5 text-muted-foreground" />
              <span>{t.newMatterModal.languageLabel}</span>
            </label>
            <Select value={language} onValueChange={(val) => setLanguage(val as "ar" | "fr" | "en")}>
              <SelectTrigger>
                <SelectValue placeholder="اختر لغة التعامل" />
              </SelectTrigger>
              <SelectContent>
                {LANGUAGES.map((lang) => (
                  <SelectItem key={lang.value} value={lang.value}>
                    {lang.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <DialogFooter className="gap-2 pt-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t.newMatterModal.cancelButton}
            </Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy ? t.newMatterModal.creatingButton : t.newMatterModal.createButton}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

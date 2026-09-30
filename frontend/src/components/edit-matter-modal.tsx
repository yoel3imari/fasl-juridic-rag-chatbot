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
import { updateMatter, type Matter } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  JURISDICTIONS,
  LANGUAGES,
  MATTER_TYPES,
  withCurrentOption,
} from "@/components/matter-options";
import { FolderCog, MapPin, Briefcase, Globe } from "lucide-react";

export function EditMatterModal({
  open,
  onOpenChange,
  matter,
  onMatterUpdated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  matter: Matter | null;
  onMatterUpdated: (matter: Matter) => void;
}) {
  const { t } = useI18n();
  const [title, setTitle] = React.useState("");
  const [jurisdiction, setJurisdiction] = React.useState("casablanca");
  const [matterType, setMatterType] = React.useState("labor");
  const [language, setLanguage] = React.useState("ar");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open || !matter) return;
    setTitle(matter.title);
    setJurisdiction(matter.jurisdiction);
    setMatterType(matter.matter_type);
    setLanguage(matter.language);
    setError(null);
  }, [open, matter]);

  const jurisdictionOptions = React.useMemo(
    () =>
      matter ? withCurrentOption(JURISDICTIONS, matter.jurisdiction) : JURISDICTIONS,
    [matter],
  );
  const matterTypeOptions = React.useMemo(
    () => (matter ? withCurrentOption(MATTER_TYPES, matter.matter_type) : MATTER_TYPES),
    [matter],
  );
  const languageOptions = React.useMemo(
    () => (matter ? withCurrentOption(LANGUAGES, matter.language) : LANGUAGES),
    [matter],
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!matter || busy) return;
    const trimmedTitle = title.trim();
    if (!trimmedTitle) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await updateMatter(matter.id, {
        title: trimmedTitle,
        matter_type: matterType,
        jurisdiction,
        language,
      });
      onMatterUpdated(updated);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.editMatterModal.errorFailed);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2 text-primary font-semibold text-base">
            <FolderCog className="h-5 w-5" />
            <span>{t.editMatterModal.modalBadge}</span>
          </div>
          <DialogTitle className="text-xl font-bold">
            {t.editMatterModal.modalTitle}
          </DialogTitle>
          <DialogDescription>{t.editMatterModal.modalDesc}</DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          {error && (
            <div
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive font-medium"
            >
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
                  <SelectValue
                    placeholder={t.newMatterModal.matterTypePlaceholder}
                  />
                </SelectTrigger>
                <SelectContent>
                  {matterTypeOptions.map((type) => (
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
                  <SelectValue
                    placeholder={t.newMatterModal.jurisdictionPlaceholder}
                  />
                </SelectTrigger>
                <SelectContent>
                  {jurisdictionOptions.map((j) => (
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
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger>
                <SelectValue
                  placeholder={t.newMatterModal.languagePlaceholder}
                />
              </SelectTrigger>
              <SelectContent>
                {languageOptions.map((lang) => (
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
              disabled={busy}
            >
              {t.editMatterModal.cancelButton}
            </Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy ? t.editMatterModal.savingButton : t.editMatterModal.saveButton}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

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
  const [title, setTitle] = React.useState("");
  const [jurisdiction, setJurisdiction] = React.useState("casablanca");
  const [matterType, setMatterType] = React.useState("labor");
  const [language, setLanguage] = React.useState("ar");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

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
            <span>فتح قضية / ملف جديد</span>
          </div>
          <DialogTitle className="text-xl font-bold">
            إنشاء ملف قضية جديد
          </DialogTitle>
          <DialogDescription>
            سجل تفاصيل القضية للبدء في رفع الوثائق، التحليل القانوني، والاستشارات.
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
              عنوان الملف / Intitulé du dossier *
            </label>
            <Input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="مثال: نزاع عقد عمل - شركة أطلس، أو تعويض عن التسريح..."
              className="text-sm"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <Briefcase className="h-3.5 w-3.5 text-muted-foreground" />
                <span>نوع النزاع / Type</span>
              </label>
              <Select value={matterType} onValueChange={setMatterType}>
                <SelectTrigger>
                  <SelectValue placeholder="اختر نوع النزاع" />
                </SelectTrigger>
                <SelectContent>
                  {MATTER_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-foreground flex items-center gap-1">
                <MapPin className="h-3.5 w-3.5 text-muted-foreground" />
                <span>الدائرة القضائية / Juridiction</span>
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
              <span>لغة العمل الرئيسية / Langue</span>
            </label>
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger>
                <SelectValue placeholder="اختر اللغة" />
              </SelectTrigger>
              <SelectContent>
                {LANGUAGES.map((l) => (
                  <SelectItem key={l.value} value={l.value}>
                    {l.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <DialogFooter className="mt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              إلغاء / Annuler
            </Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy ? "جارٍ الإنشاء…" : "+ إنشاء الملف / Créer"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

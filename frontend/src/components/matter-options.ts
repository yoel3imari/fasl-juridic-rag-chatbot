export interface MatterOption {
  value: string;
  label: string;
}

export const JURISDICTIONS: MatterOption[] = [
  { value: "casablanca", label: "الدار البيضاء / Casablanca" },
  { value: "rabat", label: "الرباط / Rabat" },
  { value: "tanger", label: "طنجة / Tanger" },
  { value: "marrakech", label: "مراكش / Marrakech" },
  { value: "fes", label: "فاس / Fès" },
  { value: "agadir", label: "أكادير / Agadir" },
  { value: "oujda", label: "وجدة / Oujda" },
];

export const MATTER_TYPES: MatterOption[] = [
  { value: "labor", label: "نزاع شغل / Droit du travail" },
  { value: "commercial", label: "قانون تجاري وشراكات / Droit commercial" },
  { value: "civil", label: "مدني وعقود / Droit civil & Contrats" },
  { value: "administrative", label: "إداري وصفقات / Droit administratif" },
  { value: "general", label: "عام / Général" },
];

export const LANGUAGES: MatterOption[] = [
  { value: "ar", label: "العربية (Arabic)" },
  { value: "fr", label: "Français (French)" },
  { value: "en", label: "English (Anglais)" },
];

export function withCurrentOption(
  options: MatterOption[],
  current: string,
): MatterOption[] {
  return options.some((option) => option.value === current)
    ? options
    : [...options, { value: current, label: current }];
}

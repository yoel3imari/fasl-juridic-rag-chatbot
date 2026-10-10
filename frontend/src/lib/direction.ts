export type TextDirection = "rtl" | "ltr";

export interface DirectionResult {
  dir: TextDirection;
  lang: string;
}

/** Arabic, Arabic Supplement, Arabic Extended-A, Arabic Presentation Forms-A/B. */
const ARABIC_RANGES: readonly (readonly [number, number])[] = [
  [0x0600, 0x06ff],
  [0x0750, 0x077f],
  [0x08a0, 0x08ff],
  [0xfb50, 0xfdff],
  [0xfe70, 0xfeff],
];

/** Hebrew and Hebrew Presentation Forms. */
const HEBREW_RANGES: readonly (readonly [number, number])[] = [
  [0x0590, 0x05ff],
  [0xfb1d, 0xfb4f],
];

/** Accented letters that mark a Latin-script message as French rather than English. */
const FRENCH_MARKERS: ReadonlySet<string> = new Set([
  "é", "è", "ê", "ë", "à", "ç", "ù", "û", "î", "ï", "ô", "â", "œ", "æ",
]);

const MULTIPLICATION_SIGN = 0x00d7;
const DIVISION_SIGN = 0x00f7;

function inRanges(code: number, ranges: readonly (readonly [number, number])[]): boolean {
  return ranges.some(([start, end]) => code >= start && code <= end);
}

/** Latin letters: ASCII A–Z/a–z plus Latin-1 letters, minus the two math signs. */
function isLatinLetter(code: number): boolean {
  if (code >= 0x41 && code <= 0x5a) return true;
  if (code >= 0x61 && code <= 0x7a) return true;
  if (code < 0x00c0 || code > 0x00ff) return false;
  return code !== MULTIPLICATION_SIGN && code !== DIVISION_SIGN;
}

/**
 * Detect the writing direction and language of a chat message from its own text.
 *
 * Majority rule: the message is RTL when RTL-script characters (Arabic or
 * Hebrew) outnumber Latin letters; otherwise it is LTR. This keeps a single
 * foreign word inside a long English answer from flipping the whole bubble,
 * and makes the result stream-safe — detection re-runs on the accumulated
 * text, so direction settles as soon as one script dominates.
 *
 * Empty or scriptless text (whitespace, digits, punctuation such as a bare
 * citation ref) falls back to LTR English, which matches the app default.
 */
export function detectDirection(text: string): DirectionResult {
  let arabic = 0;
  let hebrew = 0;
  let latin = 0;
  let french = false;

  for (const char of text) {
    const code = char.codePointAt(0);
    if (code === undefined) continue;
    if (inRanges(code, ARABIC_RANGES)) {
      arabic += 1;
    } else if (inRanges(code, HEBREW_RANGES)) {
      hebrew += 1;
    } else if (isLatinLetter(code)) {
      latin += 1;
      if (FRENCH_MARKERS.has(char.toLowerCase())) french = true;
    }
  }

  const rtl = arabic + hebrew > latin;
  if (rtl) {
    return { dir: "rtl", lang: hebrew > arabic ? "he" : "ar" };
  }
  return { dir: "ltr", lang: french ? "fr" : "en" };
}

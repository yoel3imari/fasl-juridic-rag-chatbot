import { describe, expect, it } from "vitest";
import { detectDirection } from "./direction";

describe("detectDirection", () => {
  it("returns rtl/ar when Arabic characters are the majority script", () => {
    expect(detectDirection("مرحبا بكم في القانون المغربي")).toEqual({
      dir: "rtl",
      lang: "ar",
    });
  });

  it("returns rtl/he when Hebrew characters are the majority script", () => {
    expect(detectDirection("שלום עולם")).toEqual({ dir: "rtl", lang: "he" });
  });

  it("returns ltr/en for plain English text", () => {
    expect(
      detectDirection("What is the notice period for contract termination?"),
    ).toEqual({ dir: "ltr", lang: "en" });
  });

  it("returns ltr/fr when French accents outweigh the English default", () => {
    expect(detectDirection("Quel est le délai de préavis ? réclamation")).toEqual(
      { dir: "ltr", lang: "fr" },
    );
  });

  it("falls back to ltr/en when the text is empty", () => {
    expect(detectDirection("")).toEqual({ dir: "ltr", lang: "en" });
  });

  it("falls back to ltr/en when the text is whitespace only", () => {
    expect(detectDirection("   ")).toEqual({ dir: "ltr", lang: "en" });
  });

  it("falls back to ltr/en when the text has no letters at all", () => {
    expect(detectDirection("2.04.534")).toEqual({ dir: "ltr", lang: "en" });
  });

  it("stays ltr when a long English answer carries a single Arabic word", () => {
    expect(
      detectDirection(
        "The employee is entitled to a written notice period under article 12 أحكام",
      ),
    ).toEqual({ dir: "ltr", lang: "en" });
  });

  it("flips to rtl when Arabic outweighs the Latin characters around it", () => {
    expect(
      detectDirection(
        "تنص المادة 12 من قانون الشغل على أن صاحب العمل يجب أن يخبر العامل by written notice",
      ),
    ).toEqual({ dir: "rtl", lang: "ar" });
  });

  it("ignores digits, punctuation, and Latin-1 math signs as letters", () => {
    expect(detectDirection("7 × 3 ÷ 2 = 10.5 % — 100%")).toEqual({
      dir: "ltr",
      lang: "en",
    });
  });
});

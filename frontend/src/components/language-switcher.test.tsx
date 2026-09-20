import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { LanguageSwitcher } from "./language-switcher";
import { I18nProvider, useI18n } from "@/lib/i18n";

function TestComponent() {
  const { language, dir, isRTL, t } = useI18n();
  return (
    <div>
      <LanguageSwitcher variant="toggle" />
      <span data-testid="current-lang">{language}</span>
      <span data-testid="current-dir">{dir}</span>
      <span data-testid="is-rtl">{isRTL ? "yes" : "no"}</span>
      <span data-testid="app-name">{t.common.appName}</span>
    </div>
  );
}

describe("LanguageSwitcher & I18nProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.lang = "ar";
    document.documentElement.dir = "rtl";
  });

  it("defaults to Arabic (ar) with RTL direction", () => {
    render(
      <I18nProvider>
        <TestComponent />
      </I18nProvider>,
    );

    expect(screen.getByTestId("current-lang")).toHaveTextContent("ar");
    expect(screen.getByTestId("current-dir")).toHaveTextContent("rtl");
    expect(screen.getByTestId("is-rtl")).toHaveTextContent("yes");
    expect(screen.getByTestId("app-name")).toHaveTextContent("فَصْل");
  });

  it("switches to French and updates direction to LTR", () => {
    render(
      <I18nProvider>
        <TestComponent />
      </I18nProvider>,
    );

    const frenchBtn = screen.getByRole("button", { name: /Français/i });
    fireEvent.click(frenchBtn);

    expect(screen.getByTestId("current-lang")).toHaveTextContent("fr");
    expect(screen.getByTestId("current-dir")).toHaveTextContent("ltr");
    expect(screen.getByTestId("is-rtl")).toHaveTextContent("no");
    expect(document.documentElement.lang).toBe("fr");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("switches to English and updates direction to LTR", () => {
    render(
      <I18nProvider>
        <TestComponent />
      </I18nProvider>,
    );

    const englishBtn = screen.getByRole("button", { name: /English/i });
    fireEvent.click(englishBtn);

    expect(screen.getByTestId("current-lang")).toHaveTextContent("en");
    expect(screen.getByTestId("current-dir")).toHaveTextContent("ltr");
    expect(screen.getByTestId("is-rtl")).toHaveTextContent("no");
    expect(document.documentElement.lang).toBe("en");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("renders dropdown variant without crashing", () => {
    render(
      <I18nProvider>
        <LanguageSwitcher variant="dropdown" />
      </I18nProvider>,
    );

    const trigger = screen.getByRole("button", { name: /تغيير اللغة|Changer de langue|Change Language/i });
    expect(trigger).toBeInTheDocument();
  });
});

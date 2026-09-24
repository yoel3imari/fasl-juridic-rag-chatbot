import { expect, test } from "@playwright/test";

test.describe("Library Coverage UI", () => {
  test("summary is visible and card count is bounded <= 200", async ({
    page,
  }) => {
    // Intercept coverage API to supply a known large fixture with summary
    const mockTitles = Array.from({ length: 200 }, (_, i) => ({
      source: `Code Test ${i + 1}`,
      version: "2024",
      edition: "ar-general",
      pub_date: "2024-01-01",
      chunks: 10,
    }));

    await page.route("**/api/v1/library/coverage", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          titles: mockTitles,
          gaps: ["Gap 1", "Gap 2"],
          library_version: "1.0.0",
          summary: {
            totals: {
              files: 912,
              indexed: 19,
              extracted: 69,
              embedded: 19,
              chunks: 1779,
              chunks_indexed: 254,
            },
            by_category: { "cat-a": 500, "cat-b": 412 },
            by_status: {
              index_status: { indexed: 19, pending: 893 },
            },
            by_edition: { "ar-general": 723, unparsed: 189 },
          },
          titles_truncated: 712,
          gaps_truncated: 0,
        }),
      });
    });

    await page.goto("/library");

    // Summary section should be visible
    const summarySection = page.locator('section[aria-label="Coverage summary"]');
    await expect(summarySection).toBeVisible();
    await expect(summarySection).toContainText("912");
    await expect(summarySection).toContainText("254");

    // Bounded card grid: exactly 200 cards rendered
    const cards = page.locator(".grid .rounded-2xl.border.bg-card.p-5");
    const count = await cards.count();
    expect(count).toBeLessThanOrEqual(200);
    expect(count).toBe(200);

    // Gaps section is visible
    await expect(page.getByText("Gap 1")).toBeVisible();
  });

  test("API error fixture renders non-crashing error alert", async ({
    page,
  }) => {
    await page.route("**/api/v1/library/coverage", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal Server Error" }),
      });
    });

    await page.goto("/library");

    // Error alert is rendered gracefully
    const alert = page.locator('.border-destructive\\/30[role="alert"]');
    await expect(alert).toBeVisible();

    // Page header remains intact and non-crashing
    await expect(
      page.getByRole("heading", { name: /Bibliothèque juridique/i }),
    ).toBeVisible();
  });
});

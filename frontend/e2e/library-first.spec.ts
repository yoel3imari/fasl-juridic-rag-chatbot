/**
 * Task 12 E2E: library-first + matter flow + isolation + acknowledged draft.
 *
 * Requires the live stack (`make dev`): fastapi :8000, nextjs :3000,
 * qdrant :6333, crispembed :8080, plus the labeled TEST authority point
 * from e2e/seed_test_authority.py (source E2E-TEST-LIBRARY — synthetic
 * plumbing proof, NOT legal content; task 3 awaits real curated files).
 */
import { expect, test } from "@playwright/test";

const API = "http://localhost:8000";

const LETTER =
  "Termination letter. Mr. Karim Bennani, your employment contract is " +
  "terminated effective 2024-03-15. Your notice period is 1 month. Final " +
  "compensation of 15000 MAD will be paid. Signed by Societe Atlas SARL.";

async function createMatter(request: any, title: string): Promise<number> {
  const res = await request.post(`${API}/api/v1/matters`, {
    data: {
      title,
      matter_type: "labor",
      jurisdiction: "casablanca",
      language: "ar",
    },
  });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { id: number }).id;
}

test("library-first: zero-upload query cites TEST authority with version+edition", async ({
  request,
}) => {
  const res = await request.post(`${API}/api/v1/search`, {
    data: {
      query: "annual leave period test worker",
      domain: "authority",
    },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.authority.length).toBeGreaterThan(0);
  const hit = body.authority[0];
  expect(hit.source).toBe("E2E-TEST-LIBRARY");
  expect(hit.version).toBe("2099-test");
  expect(hit.edition).toBe("ar-general");
  expect(hit.article_or_section).toBe("TEST-ARTICLE-1");
});

test("matter flow: upload -> analysis risks+gaps -> dual query -> isolation", async ({
  request,
}) => {
  const matterA = await createMatter(request, "E2E matter A");
  const up = await request.post(
    `${API}/api/v1/matters/${matterA}/documents/upload`,
    {
      multipart: {
        file: {
          name: "Termination_Letter.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(LETTER, "utf-8"),
        },
      },
    },
  );
  expect(up.ok()).toBeTruthy();
  expect((await up.json()).indexed_count).toBe(1);

  const an = await request.post(
    `${API}/api/v1/matters/${matterA}/analysis`,
    { data: {} },
  );
  expect(an.ok()).toBeTruthy();
  const analysis = await an.json();
  expect(analysis.content.issues.length).toBeGreaterThanOrEqual(3);
  for (const row of analysis.content.issues) {
    expect(["High", "Medium", "Low"]).toContain(row.risk);
    expect(row.span_refs.length).toBeGreaterThan(0);
  }
  const gaps = analysis.content.gaps.join(" ").toLowerCase();
  expect(gaps).toContain("payslip");

  const q = await request.post(`${API}/api/v1/search`, {
    data: { query: "notice period", domain: "both", matter_id: matterA },
  });
  expect(q.ok()).toBeTruthy();
  const both = await q.json();
  expect(Object.keys(both).sort()).toEqual(
    expect.arrayContaining(["authority", "matter"]),
  );
  expect(both.matter.length).toBeGreaterThan(0);
  expect(both.matter[0].domain ?? "matter").toBe("matter");

  // Cross-matter negative: a second matter sees nothing of A.
  const matterB = await createMatter(request, "E2E matter B");
  const qb = await request.post(`${API}/api/v1/search`, {
    data: { query: "notice period", domain: "matter", matter_id: matterB },
  });
  expect(qb.ok()).toBeTruthy();
  expect((await qb.json()).matter).toEqual([]);
});

test("acknowledged draft flow renders zero 'lawyer review' strings", async ({
  request,
}) => {
  const matter = await createMatter(request, "E2E draft matter");
  await request.post(`${API}/api/v1/matters/${matter}/documents/upload`, {
    multipart: {
      file: {
        name: "Letter.txt",
        mimeType: "text/plain",
        buffer: Buffer.from(LETTER, "utf-8"),
      },
    },
  });
  await request.post(`${API}/api/v1/matters/${matter}/analysis`, { data: {} });

  const dr = await request.post(`${API}/api/v1/matters/${matter}/drafts`, {
    data: { draft_type: "demand_letter" },
  });
  expect(dr.status()).toBe(201);
  const draft = await dr.json();
  const ack = await request.post(
    `${API}/api/v1/drafts/${draft.draft_id}/acknowledge`,
  );
  expect(ack.ok()).toBeTruthy();
  const ackBody = await ack.json();
  expect(ackBody.review_state).toBe("acknowledged");
  expect(JSON.stringify(ackBody).toLowerCase()).not.toContain("lawyer review");
});

test("browser smoke: RTL Arabic home + library page", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.getByLabel("question")).toBeVisible();

  await page.goto("/library");
  await expect(
    page.getByRole("heading", { name: /Bibliothèque juridique/ }),
  ).toBeVisible();
});

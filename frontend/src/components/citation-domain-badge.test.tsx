/**
 * TDD (red-first) for citation-domain-badge (task 10).
 *
 * Given: backend emits per-claim citations with domain: matter|authority.
 * When: the UI renders a claim's citation card.
 * Then: the card shows exactly one domain badge (matter vs authority),
 *       the correct human reference, and never a relevance score / rank.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type {
  AuthorityCitation,
  MatterCitation,
} from "@/lib/api";
import { CitationDomainBadge } from "./citation-domain-badge";

const MATTER_CITATION: MatterCitation = {
  domain: "matter",
  document_id: 3,
  version_no: 1,
  doc_type: "letter",
  page: 2,
  span: [0, 44],
  faithful_ref: "sec-1",
};

const AUTHORITY_CITATION: AuthorityCitation = {
  domain: "authority",
  source: "Code du Travail",
  version: "2023",
  edition: "ar-general",
  pub_date: "2023-01-01",
  doc_date: null,
  language: "ar",
  article_or_section: "Article 237",
};

describe("CitationDomainBadge", () => {
  it("renders a matter badge with doc/page reference for matter claims", () => {
    render(<CitationDomainBadge citation={MATTER_CITATION} />);
    const badge = screen.getByTestId("domain-badge-matter");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(/matter/i);
    expect(screen.queryByTestId("domain-badge-authority")).toBeNull();
    expect(screen.getByText(/doc 3/i)).toBeInTheDocument();
    expect(screen.getByText(/p\.2/i)).toBeInTheDocument();
  });

  it("renders an authority badge with article + version + edition", () => {
    render(<CitationDomainBadge citation={AUTHORITY_CITATION} />);
    const badge = screen.getByTestId("domain-badge-authority");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(/authority/i);
    expect(screen.queryByTestId("domain-badge-matter")).toBeNull();
    expect(screen.getByText(/Article 237/)).toBeInTheDocument();
    expect(screen.getByText(/2023/)).toBeInTheDocument();
    expect(screen.getByText(/ar-general/)).toBeInTheDocument();
  });

  it("never renders relevance scores or authority ranks", () => {
    const { container } = render(
      <CitationDomainBadge citation={AUTHORITY_CITATION} />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/score/i);
    expect(text).not.toMatch(/rank/i);
    expect(text).not.toMatch(/relevance/i);
  });
});

/**
 * TDD (red-first) for retrieved-chunks-panel.
 *
 * Given: the chat stream emits claim citations from two domains (private matter
 *        evidence and the versioned authority library), each carrying an
 *        optional excerpt.
 * When: the workspace renders RetrievedChunksPanel for that answer.
 * Then: chunks are grouped by domain and empty groups are omitted, every chunk
 *       is a collapsed-by-default card revealing its excerpt (or a "no excerpt"
 *       fallback), the view-details action appears only when an onSelect
 *       handler was supplied, and expand/collapse-all drive groups as well as
 *       cards so a collapsed group can never swallow an "expand all".
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  AuthorityCitation,
  Citation,
  Domain,
  MatterCitation,
} from "@/lib/api";
import { I18nProvider, useI18n } from "@/lib/i18n";
import { RetrievedChunksPanel } from "./retrieved-chunks-panel";

// `t` is typed as the per-language union of `as const` dictionaries, so this
// must stay a union: `Translations["retrievedChunks"]` (the `ar` shape alone)
// does not assign.
type Copy = ReturnType<typeof useI18n>["t"]["retrievedChunks"];

const MATTER_EXCERPT_TEXT =
  "تمت تسوية العقد كتابةً قبل انتهاء أجل الإخطار.";
const AUTHORITY_EXCERPT_TEXT =
  "لا يمكن إقفال العقد قبل تصفية المستحقات المترتبة على علاقة العمل.";

const MATTER_WITH_EXCERPT: MatterCitation = {
  domain: "matter",
  document_id: 3,
  version_no: 1,
  doc_type: "letter",
  page: 2,
  span: [0, 44],
  faithful_ref: "sec-1",
  excerpt: MATTER_EXCERPT_TEXT,
};

const MATTER_WITHOUT_EXCERPT: MatterCitation = {
  domain: "matter",
  document_id: 5,
  version_no: 1,
  doc_type: "contract",
  page: 7,
  span: [12, 30],
  faithful_ref: "sec-4",
};

const MATTER_BLANK_EXCERPT: MatterCitation = {
  domain: "matter",
  document_id: 9,
  version_no: 2,
  doc_type: "report",
  page: 1,
  span: [0, 8],
  faithful_ref: "sec-0",
  excerpt: "   ",
};

const AUTHORITY_WITH_EXCERPT: AuthorityCitation = {
  domain: "authority",
  source: "Code du Travail",
  version: "2023",
  edition: "ar-general",
  pub_date: "2023-01-01",
  doc_date: null,
  language: "ar",
  article_or_section: "Article 237",
  excerpt: AUTHORITY_EXCERPT_TEXT,
};

const AUTHORITY_WITHOUT_EXCERPT: AuthorityCitation = {
  domain: "authority",
  source: "Loi 12-03",
  version: "2015",
  edition: "fr-officiel",
  pub_date: null,
  doc_date: null,
  language: "fr",
  article_or_section: "Article 3",
};

// Spelled out by hand against the label contract (`[matter: doc N p.P ¶A–B]`,
// `[authority: X vY] · source · edition`) so the assertions pin the rendered
// ref text instead of recomputing it with the very helpers the panel calls.
const MATTER_REF = "[matter: doc 3 p.2 ¶0–44]";
const MATTER_ALT_REF = "[matter: doc 5 p.7 ¶12–30]";
const MATTER_BLANK_REF = "[matter: doc 9 p.1 ¶0–8]";
const AUTHORITY_REF =
  "[authority: Article 237 v2023] · Code du Travail · ar-general";
const AUTHORITY_ALT_REF =
  "[authority: Article 3 v2015] · Loi 12-03 · fr-officiel";

/** Copy is read from the provider so no assertion ever hard-codes UI prose. */
let captured: Copy | null = null;

function CopyProbe() {
  captured = useI18n().t.retrievedChunks;
  return null;
}

function renderPanel(
  chunks: Citation[],
  onSelect?: (citation: Citation) => void,
): Copy {
  render(
    <I18nProvider>
      <CopyProbe />
      <RetrievedChunksPanel chunks={chunks} {...(onSelect ? { onSelect } : {})} />
    </I18nProvider>,
  );
  if (captured === null) throw new Error("I18nProvider supplied no copy");
  return captured;
}

/** Group headers are the only controls pointing at `retrieved-chunks-group-*`. */
function groupHeader(domain: Domain): HTMLElement | null {
  const wanted = `retrieved-chunks-group-${domain}`;
  return (
    screen
      .queryAllByRole("button")
      .find((button) => button.getAttribute("aria-controls") === wanted) ??
    null
  );
}

function requireGroupHeader(domain: Domain): HTMLElement {
  const header = groupHeader(domain);
  if (header === null) throw new Error(`no ${domain} group header rendered`);
  return header;
}

/** Resolves the region a disclosure button claims to control. */
function controlledRegion(button: HTMLElement): HTMLElement {
  const id = button.getAttribute("aria-controls");
  if (id === null) throw new Error("control has no aria-controls");
  const region = document.getElementById(id);
  if (region === null) throw new Error(`nothing rendered for ${id}`);
  return region;
}

function groupRegion(domain: Domain): HTMLElement {
  return controlledRegion(requireGroupHeader(domain));
}

function cardHeader(ref: string): HTMLElement {
  const button = screen.getByText(ref).closest("button");
  if (button === null) throw new Error(`${ref} is not inside a card header`);
  return button;
}

function cardRegion(ref: string): HTMLElement {
  return controlledRegion(cardHeader(ref));
}

/** Excerpt-or-fallback paragraphs; a blank excerpt would be invisible to text queries. */
function paragraphs(root: HTMLElement): Array<string | null> {
  return Array.from(root.querySelectorAll("p")).map((node) => node.textContent);
}

function expectExpanded(control: HTMLElement, expanded: boolean): void {
  expect(control).toHaveAttribute("aria-expanded", String(expanded));
}

describe("RetrievedChunksPanel", () => {
  it("renders the empty copy with no groups and no expand controls for an empty list", () => {
    const copy = renderPanel([]);

    expect(screen.getByText(copy.empty)).toBeInTheDocument();
    expect(groupHeader("matter")).toBeNull();
    expect(groupHeader("authority")).toBeNull();
    expect(screen.queryByRole("button", { name: copy.expandAll })).toBeNull();
  });

  it("lists every supplied chunk", () => {
    renderPanel([
      MATTER_WITH_EXCERPT,
      MATTER_WITHOUT_EXCERPT,
      MATTER_BLANK_EXCERPT,
      AUTHORITY_WITH_EXCERPT,
      AUTHORITY_WITHOUT_EXCERPT,
    ]);

    for (const ref of [
      MATTER_REF,
      MATTER_ALT_REF,
      MATTER_BLANK_REF,
      AUTHORITY_REF,
      AUTHORITY_ALT_REF,
    ]) {
      expect(screen.getByText(ref)).toBeInTheDocument();
    }
  });

  it("places each chunk inside its own domain group", () => {
    renderPanel([MATTER_WITH_EXCERPT, AUTHORITY_WITH_EXCERPT]);

    const matter = groupRegion("matter");
    const authority = groupRegion("authority");

    expect(within(matter).getByText(MATTER_REF)).toBeInTheDocument();
    expect(within(matter).queryByText(AUTHORITY_REF)).toBeNull();
    expect(within(authority).getByText(AUTHORITY_REF)).toBeInTheDocument();
    expect(within(authority).queryByText(MATTER_REF)).toBeNull();
  });

  it("omits the header of a domain that has no chunks", () => {
    renderPanel([MATTER_WITH_EXCERPT]);

    expect(groupHeader("matter")).not.toBeNull();
    expect(groupHeader("authority")).toBeNull();
    expect(screen.queryByText(AUTHORITY_ALT_REF)).toBeNull();
  });

  it("hides and restores a group's chunks when its header is toggled", () => {
    renderPanel([MATTER_WITH_EXCERPT, AUTHORITY_WITH_EXCERPT]);

    fireEvent.click(requireGroupHeader("matter"));

    expectExpanded(requireGroupHeader("matter"), false);
    expect(screen.queryByText(MATTER_REF)).toBeNull();
    expectExpanded(requireGroupHeader("authority"), true);

    fireEvent.click(requireGroupHeader("matter"));

    expectExpanded(requireGroupHeader("matter"), true);
    expect(screen.getByText(MATTER_REF)).toBeInTheDocument();
  });

  it("expands one chunk card at a time and leaves its siblings collapsed", () => {
    const copy = renderPanel([MATTER_WITH_EXCERPT, MATTER_WITHOUT_EXCERPT]);

    expectExpanded(cardHeader(MATTER_REF), false);
    expectExpanded(cardHeader(MATTER_ALT_REF), false);
    expect(screen.queryByText(MATTER_EXCERPT_TEXT)).toBeNull();

    fireEvent.click(cardHeader(MATTER_REF));

    expectExpanded(cardHeader(MATTER_REF), true);
    expect(screen.getByText(MATTER_EXCERPT_TEXT)).toBeInTheDocument();
    expectExpanded(cardHeader(MATTER_ALT_REF), false);
    expect(screen.queryByText(copy.noExcerpt)).toBeNull();

    fireEvent.click(cardHeader(MATTER_REF));

    expectExpanded(cardHeader(MATTER_REF), false);
    expect(screen.queryByText(MATTER_EXCERPT_TEXT)).toBeNull();
  });

  it("shows the excerpt when present and the fallback when absent or blank", () => {
    const copy = renderPanel([
      MATTER_WITH_EXCERPT,
      MATTER_WITHOUT_EXCERPT,
      MATTER_BLANK_EXCERPT,
    ]);

    for (const ref of [MATTER_REF, MATTER_ALT_REF, MATTER_BLANK_REF]) {
      fireEvent.click(cardHeader(ref));
    }

    expect(screen.getByText(MATTER_EXCERPT_TEXT)).toBeInTheDocument();
    expect(paragraphs(cardRegion(MATTER_REF))).toEqual([MATTER_EXCERPT_TEXT]);
    expect(paragraphs(cardRegion(MATTER_ALT_REF))).toEqual([copy.noExcerpt]);
    // A whitespace-only excerpt is trimmed to nothing, so it must fall back
    // rather than render an empty paragraph.
    expect(paragraphs(cardRegion(MATTER_BLANK_REF))).toEqual([copy.noExcerpt]);
  });

  it("hands the clicked citation to onSelect from the view-details action", () => {
    const onSelect = vi.fn<(citation: Citation) => void>();
    const copy = renderPanel([AUTHORITY_WITH_EXCERPT], onSelect);

    fireEvent.click(cardHeader(AUTHORITY_REF));
    fireEvent.click(screen.getByRole("button", { name: copy.viewDetails }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toBe(AUTHORITY_WITH_EXCERPT);
  });

  it("renders no view-details action when no onSelect handler is supplied", () => {
    const copy = renderPanel([AUTHORITY_WITH_EXCERPT]);

    fireEvent.click(cardHeader(AUTHORITY_REF));

    expect(cardRegion(AUTHORITY_REF)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.viewDetails })).toBeNull();
  });

  it("expand-all re-opens collapsed groups and collapse-all clears groups and cards", () => {
    const copy = renderPanel([MATTER_WITH_EXCERPT, AUTHORITY_WITH_EXCERPT]);

    // Start from the default (groups open, cards collapsed) with the matter
    // group manually collapsed: an expand-all that ignores group state would
    // leave those cards mounted-but-hidden behind a collapsed group.
    fireEvent.click(requireGroupHeader("matter"));
    expectExpanded(requireGroupHeader("matter"), false);

    fireEvent.click(screen.getByRole("button", { name: copy.expandAll }));

    expectExpanded(requireGroupHeader("matter"), true);
    expectExpanded(requireGroupHeader("authority"), true);
    expectExpanded(cardHeader(MATTER_REF), true);
    expectExpanded(cardHeader(AUTHORITY_REF), true);

    fireEvent.click(screen.getByRole("button", { name: copy.collapseAll }));

    expectExpanded(requireGroupHeader("matter"), false);
    expectExpanded(requireGroupHeader("authority"), false);

    // Re-open the groups to read the card state collapse-all left behind.
    fireEvent.click(requireGroupHeader("matter"));
    fireEvent.click(requireGroupHeader("authority"));

    expectExpanded(cardHeader(MATTER_REF), false);
    expectExpanded(cardHeader(AUTHORITY_REF), false);
  });
});

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Upload } from "./upload";
import { I18nProvider } from "@/lib/i18n";
import * as api from "@/lib/api";
import type { MatterDocument } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listMatterDocuments: vi.fn(),
    deleteMatterDocument: vi.fn(),
  };
});

const listMatterDocuments = vi.mocked(api.listMatterDocuments);
const deleteMatterDocument = vi.mocked(api.deleteMatterDocument);

const contract: MatterDocument = {
  document_id: 7,
  original_name: "contrat-travail.pdf",
  filename: "contrat-travail.pdf",
  doc_type: "pdf",
  status: "indexed",
  needs_review: false,
  chunk_count: 42,
  section_count: 5,
  page_count: 12,
  created_at: "2026-09-22T10:00:00Z",
};

function renderUpload(matterId: number | null = 1) {
  return render(
    <I18nProvider>
      <Upload matterId={matterId} />
    </I18nProvider>,
  );
}

describe("Upload document delete flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("fasl_language", "en");
  });

  it("shows a loading state and then renders the persisted document row", async () => {
    let release: ((docs: MatterDocument[]) => void) | undefined;
    listMatterDocuments.mockReturnValueOnce(
      new Promise<MatterDocument[]>((resolve) => {
        release = resolve;
      }),
    );

    renderUpload();

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading matter documents…",
    );

    release?.([contract]);

    await waitFor(() => {
      expect(screen.getByText("contrat-travail.pdf")).toBeInTheDocument();
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(listMatterDocuments).toHaveBeenCalledWith(1);
    expect(screen.getByText(/5 extracted sections/)).toBeInTheDocument();
    expect(screen.getByText("Indexed")).toBeInTheDocument();
  });

  it("shows the empty-state message when the matter has no documents", async () => {
    listMatterDocuments.mockResolvedValueOnce([]);
    renderUpload();

    await waitFor(() => {
      expect(
        screen.getByText("No documents uploaded in this matter yet."),
      ).toBeInTheDocument();
    });
  });

  it("deletes the row through the confirmation dialog and drops it on success", async () => {
    listMatterDocuments.mockResolvedValue([contract]);
    deleteMatterDocument.mockResolvedValue({
      status: "deleted",
      document_id: 7,
      removed_points: 42,
      removed_files: 1,
    });

    renderUpload();

    await waitFor(() => {
      expect(screen.getByText("contrat-travail.pdf")).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Delete document contrat-travail.pdf" }),
    );

    expect(
      screen.getByRole("heading", { name: "Confirm document deletion" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));

    await waitFor(() => {
      expect(deleteMatterDocument).toHaveBeenCalledWith(1, 7);
    });
    await waitFor(() => {
      expect(screen.queryByText("contrat-travail.pdf")).not.toBeInTheDocument();
    });
  });

  it("keeps the row and surfaces the error when the delete request fails", async () => {
    listMatterDocuments.mockResolvedValue([contract]);
    deleteMatterDocument.mockRejectedValue(
      new api.ApiError(500, "document is still being indexed"),
    );

    renderUpload();

    await waitFor(() => {
      expect(screen.getByText("contrat-travail.pdf")).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Delete document contrat-travail.pdf" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));

    await waitFor(() => {
      expect(deleteMatterDocument).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "500: API 500: document is still being indexed",
      );
    });
    expect(screen.getByText("contrat-travail.pdf")).toBeInTheDocument();
  });
});

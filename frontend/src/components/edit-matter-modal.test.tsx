import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { EditMatterModal } from "./edit-matter-modal";
import { I18nProvider } from "@/lib/i18n";
import * as api from "@/lib/api";
import type { Matter } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    updateMatter: vi.fn(),
  };
});

const updateMatter = vi.mocked(api.updateMatter);

const matter: Matter = {
  id: 1,
  title: "Atlas SARL Dispute",
  matter_type: "labor",
  jurisdiction: "casablanca",
  language: "ar",
};

function Harness() {
  const [current, setCurrent] = useState<Matter>(matter);
  const [open, setOpen] = useState(true);
  return (
    <div>
      <span data-testid="matter-title">{current.title}</span>
      <I18nProvider>
        <EditMatterModal
          open={open}
          onOpenChange={setOpen}
          matter={current}
          onMatterUpdated={setCurrent}
        />
      </I18nProvider>
    </div>
  );
}

describe("EditMatterModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("fasl_language", "en");
  });

  it("pre-fills the current matter values", async () => {
    updateMatter.mockResolvedValue(matter);
    render(<Harness />);

    await waitFor(() => {
      expect(screen.getByDisplayValue("Atlas SARL Dispute")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("heading", { name: "Edit Matter Details" }),
    ).toBeInTheDocument();
  });

  it("patches the edited title and reflects the saved value", async () => {
    updateMatter.mockImplementation(async (_id, patch) => ({
      ...matter,
      ...patch,
    }));
    render(<Harness />);

    const titleInput = await screen.findByDisplayValue("Atlas SARL Dispute");
    fireEvent.change(titleInput, { target: { value: "Atlas SARL — Renegotiated" } });

    const saveButton = screen.getByRole("button", { name: "Save changes" });
    expect(saveButton.getAttribute("type")).toBe("submit");

    fireEvent.submit(titleInput.closest("form") as HTMLFormElement);

    await waitFor(() => {
      expect(updateMatter).toHaveBeenCalledWith(1, {
        title: "Atlas SARL — Renegotiated",
        matter_type: "labor",
        jurisdiction: "casablanca",
        language: "ar",
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("matter-title")).toHaveTextContent(
        "Atlas SARL — Renegotiated",
      );
    });
  });

  it("surfaces the error and keeps the dialog open when the patch fails", async () => {
    updateMatter.mockRejectedValue(new api.ApiError(409, "title already used"));
    render(<Harness />);

    const titleInput = await screen.findByDisplayValue("Atlas SARL Dispute");
    fireEvent.change(titleInput, { target: { value: "Duplicate" } });
    fireEvent.submit(titleInput.closest("form") as HTMLFormElement);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("409");
    });
    expect(
      screen.getByRole("heading", { name: "Edit Matter Details" }),
    ).toBeInTheDocument();
  });
});

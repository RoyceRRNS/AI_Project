import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CitationReport } from "../api";
import { Markdown, MathText } from "./Markdown";

describe("Markdown", () => {
  it("renders inline and display math with KaTeX", () => {
    const { container } = render(<Markdown text={"Inline $x^2$ and\n\n$$\n\\frac{1}{2}\n$$"} />);
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector(".katex-display")).not.toBeNull();
  });

  it("renders GFM tables inside a scroll wrapper", () => {
    const { container } = render(<Markdown text={"|a|b|\n|---|---|\n|1|2|"} />);
    expect(container.querySelector(".table-wrap table")).not.toBeNull();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renders citations as clickable chips", () => {
    const onCite = vi.fn();
    render(<Markdown text="Claim [Source: Lecture_04_SVMs.pdf, p. 2]." onCite={onCite} />);
    const chip = screen.getByRole("button", { name: /Lecture 04 SVMs, page 2/ });
    fireEvent.click(chip);
    expect(onCite).toHaveBeenCalledWith(expect.objectContaining({ page: 2, pdfName: "Lecture_04_SVMs.pdf" }));
  });

  it("disables chips that failed verification", () => {
    const report: CitationReport = {
      citations: [], valid_count: 0, invalid_count: 1, uncited_sentences: [], claim_count: 1, coverage: 0, grounded: false,
    };
    render(<Markdown text="Claim [Source: Ghost.pdf, p. 9]." report={report} />);
    const chip = screen.getByRole("button", { name: /Ghost, page 9/ });
    expect(chip).toBeDisabled();
    expect(chip).toHaveClass("cite-invalid");
  });

  it("opens ordinary links in a new tab", () => {
    render(<Markdown text="[site](https://example.com)" />);
    expect(screen.getByRole("link", { name: "site" })).toHaveAttribute("target", "_blank");
  });

  it("renders a parsed formula with rows but no environment instead of showing its source", () => {
    const { container } = render(<Markdown text={"$$\nk x w + ( b - & low ) \\\\ = k x 4\n$$"} />);
    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.querySelector(".katex-error")).toBeNull();
  });

  it("shows a note instead of red source code for a formula that cannot be rendered", () => {
    const { container } = render(<Markdown text={"Address:\n\n$$\n\\begin{array} {\n$$\n\nNext [Source: Notes.pdf, p. 3]."} />);
    expect(container.querySelector(".katex-error")).toBeNull();
    const note = screen.getByText(/couldn't be read from the PDF/);
    expect(note.closest(".formula-unreadable")).toHaveClass("display");
    expect(note.closest(".formula-unreadable")).toHaveAttribute("title", "What the parser read: \\begin{array} {");
    expect(screen.getByRole("button", { name: /Notes, page 3/ })).toBeInTheDocument();
  });

  it("lets the caller choose what stands in for an unreadable formula", () => {
    render(<Markdown text={"$$\n\\frac{1}{\n$$"} renderUnreadable={(latex) => <em>scan of {latex}</em>} />);
    expect(screen.getByText("scan of \\frac{1}{")).toBeInTheDocument();
    expect(screen.queryByText(/couldn't be read/)).toBeNull();
  });
});

describe("MathText", () => {
  it("renders short math strings inline", () => {
    const { container } = render(<MathText text="Compute $\\nabla f$" />);
    expect(container.querySelector(".math-text .katex")).not.toBeNull();
  });

  it("shows an inline note for a formula that cannot be rendered", () => {
    const { container } = render(<MathText text={"“The address is $\\frac{1}{$.”"} />);
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(screen.getByText(/couldn't be read from the PDF/).closest(".formula-unreadable")).not.toHaveClass("display");
  });
});

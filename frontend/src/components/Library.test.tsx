import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mockBackend } from "../test/backend";
import { doc, renderWithStore } from "../test/fixtures";
import { EmptyCourses, Library, validateFiles } from "./Library";

const pdf = (name = "notes.pdf", size = 10) => new File([new Uint8Array(size)], name, { type: "application/pdf" });

describe("validateFiles", () => {
  it("accepts PDFs and rejects other, empty or oversized files", () => {
    const big = pdf("big.pdf");
    Object.defineProperty(big, "size", { value: 101 * 1024 * 1024 });
    const { accepted, rejected } = validateFiles([
      pdf(), new File(["x"], "image.png", { type: "image/png" }), pdf("empty.pdf", 0), big,
    ]);
    expect(accepted.map((f) => f.name)).toEqual(["notes.pdf"]);
    expect(rejected).toEqual(["image.png is not a PDF", "empty.pdf is empty", "big.pdf is larger than 100 MB"]);
  });
});

describe("Library", () => {
  beforeEach(() => localStorage.clear());

  it("lists documents with their status and stats", async () => {
    mockBackend({
      listDocuments: vi.fn().mockResolvedValue([
        doc(),
        doc({ id: "d2", pdf_name: "Slides.pdf", is_slides: true, status: "parsing", progress: 0.2, num_parents: 0 }),
        doc({ id: "d3", pdf_name: "Broken.pdf", status: "failed", error: "password protected" }),
      ]),
    });
    renderWithStore(<Library />);
    const cards = await screen.findAllByTestId("doc-card");
    expect(cards).toHaveLength(3);
    expect(within(cards[0]).getByText(/Ready for chat/)).toBeInTheDocument();
    expect(within(cards[1]).getByLabelText(/Status: Parsing layout/)).toBeInTheDocument();
    expect(within(cards[1]).getByText("Slides", { selector: ".badge" })).toBeInTheDocument();
    expect(within(cards[2]).getByRole("alert")).toHaveTextContent("password protected");
    expect(screen.getByLabelText("Library summary")).toHaveTextContent("3 documents");
  });

  it("uploads valid PDFs and reports rejected files", async () => {
    const mocks = mockBackend({
      uploadDocuments: vi.fn().mockResolvedValue({
        documents: [doc({ id: "n1", status: "queued" })],
        errors: [{ filename: "dup.pdf", detail: "dup.pdf is already in this course", status: 409 }],
      }),
    });
    renderWithStore(<Library />);
    await screen.findAllByTestId("doc-card");
    fireEvent.change(screen.getByTestId("file-input"), {
      target: { files: [pdf("good.pdf"), new File(["x"], "bad.txt", { type: "text/plain" })] },
    });
    await waitFor(() => expect(mocks.uploadDocuments).toHaveBeenCalledTimes(1));
    expect((mocks.uploadDocuments.mock.calls[0][1] as File[]).map((f) => f.name)).toEqual(["good.pdf"]);
    expect(await screen.findByText("bad.txt is not a PDF")).toBeInTheDocument();
    expect(await screen.findByText("dup.pdf is already in this course")).toBeInTheDocument();
  });

  it("supports drag and drop", async () => {
    const mocks = mockBackend();
    renderWithStore(<Library />);
    await screen.findAllByTestId("doc-card");
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [pdf("dropped.pdf")] } });
    await waitFor(() => expect(mocks.uploadDocuments).toHaveBeenCalled());
  });

  it("shows server errors from a failed upload", async () => {
    mockBackend({ uploadDocuments: vi.fn().mockRejectedValue(new Error("x.pdf is not a PDF file")) });
    renderWithStore(<Library />);
    await screen.findAllByTestId("doc-card");
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [pdf("x.pdf")] } });
    expect(await screen.findByText("x.pdf is not a PDF file")).toBeInTheDocument();
  });

  it("asks for confirmation before deleting", async () => {
    const mocks = mockBackend();
    renderWithStore(<Library />);
    await screen.findAllByTestId("doc-card");
    fireEvent.click(screen.getByRole("button", { name: "Delete Lecture_04_SVMs.pdf" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm delete" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mocks.deleteDocument).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete Lecture_04_SVMs.pdf" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mocks.deleteDocument).toHaveBeenCalledWith("d1"));
  });

  it("previews extracted markdown with rendered math", async () => {
    mockBackend();
    renderWithStore(<Library />);
    await screen.findAllByTestId("doc-card");
    fireEvent.click(screen.getByRole("button", { name: "Preview Lecture_04_SVMs.pdf" }));
    const drawer = await screen.findByRole("dialog", { name: "Extracted content" });
    expect(within(drawer).getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(drawer.querySelector(".katex")).not.toBeNull();
    fireEvent.click(within(drawer).getByRole("button", { name: "Close preview" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("retries a failed document", async () => {
    const mocks = mockBackend({ listDocuments: vi.fn().mockResolvedValue([doc({ status: "failed", error: "boom" })]) });
    renderWithStore(<Library />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry Lecture_04_SVMs.pdf" }));
    await waitFor(() => expect(mocks.reprocessDocument).toHaveBeenCalledWith("d1"));
  });

  it("polls while documents are processing and announces when ready", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce([doc({ status: "indexing", progress: 0.7 })])
      .mockResolvedValue([doc()]);
    mockBackend({ listDocuments: list });
    renderWithStore(<Library />);
    expect(await screen.findByText("Lecture_04_SVMs.pdf is ready")).toBeInTheDocument();
    expect(list.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

describe("EmptyCourses", () => {
  it("creates the sample course", async () => {
    const mocks = mockBackend({
      listCourses: vi.fn().mockResolvedValue([]),
      createSampleCourse: vi.fn().mockResolvedValue({ course: { id: "s1" }, documents: [] }),
    });
    const onCreate = vi.fn();
    renderWithStore(<EmptyCourses onCreate={onCreate} />);
    fireEvent.click(screen.getByRole("button", { name: /Try the sample course/ }));
    await waitFor(() => expect(mocks.createSampleCourse).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Create a course" }));
    expect(onCreate).toHaveBeenCalled();
  });
});

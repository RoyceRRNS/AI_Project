import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { mockBackend } from "../test/backend";
import { renderWithStore, source, streamOf } from "../test/fixtures";
import { Chat, loadHistory } from "./Chat";

const ANSWER = "Complementary slackness means $\\alpha_i g_i = 0$ [Source: Lecture_04_SVMs.pdf, p. 2].";
const REPORT = {
  citations: [{ raw: "", pdf_name: "Lecture_04_SVMs.pdf", page: 2, page_end: 2, start: 0, end: 0, valid: true, reason: null,
    document_id: "d1", resolved_pdf: "Lecture_04_SVMs.pdf", bboxes: [{ page: 2, x0: 1, y0: 2, x1: 3, y1: 4 }] }],
  valid_count: 1, invalid_count: 0, uncited_sentences: [], claim_count: 1, coverage: 1, grounded: true,
};

function ViewerProbe() {
  const { viewer } = useStore();
  return <div data-testid="viewer-probe">{viewer ? `${viewer.documentId}:${viewer.page}:${viewer.bboxes.length}` : "closed"}</div>;
}

async function ask(text: string) {
  const box = await screen.findByLabelText("Your question");
  await waitFor(() => expect(box).not.toBeDisabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

describe("Chat", () => {
  beforeEach(() => localStorage.clear());

  it("streams a grounded answer with verified citation chips", async () => {
    const chat = streamOf([
      { event: "status", data: { stage: "retrieving" } },
      { event: "sources", data: { sources: [source], expanded_queries: [] } },
      { event: "status", data: { stage: "generating" } },
      { event: "token", data: { text: ANSWER.slice(0, 30) } },
      { event: "token", data: { text: ANSWER.slice(30) } },
      { event: "citations", data: REPORT },
      { event: "done", data: { answer: ANSWER, not_found: false } },
    ]);
    mockBackend({ chat });
    renderWithStore(<><Chat /><ViewerProbe /></>);
    await ask("What is complementary slackness?");
    expect(await screen.findByTestId("grounding")).toHaveTextContent("1 citation verified");
    expect(screen.getByText("What is complementary slackness?")).toBeInTheDocument();
    expect(chat.mock.calls[0][0]).toMatchObject({ course_id: "c1", question: "What is complementary slackness?", expansion: "none" });

    const chip = screen.getByRole("button", { name: /Lecture 04 SVMs, page 2/ });
    expect(chip).toHaveClass("cite-valid");
    fireEvent.click(chip);
    expect(screen.getByTestId("viewer-probe")).toHaveTextContent("d1:2:1");

    fireEvent.click(screen.getByRole("button", { name: /1 source/ }));
    fireEvent.click(screen.getByRole("button", { name: /3.2 KKT Conditions/ }));
    expect(screen.getByTestId("viewer-probe")).toHaveTextContent("d1:2:1");
  });

  it("replaces the answer with the annotated version and marks auto-added citations", async () => {
    const annotated = `${ANSWER} Gates control the cell [Source: Lecture_04_SVMs.pdf, p. 3].`;
    const report = {
      ...REPORT, inferred_count: 1, annotated_answer: annotated,
      citations: [REPORT.citations[0], { ...REPORT.citations[0], page: 3, inferred: true }],
      valid_count: 2,
    };
    mockBackend({
      chat: streamOf([
        { event: "sources", data: { sources: [source] } },
        { event: "token", data: { text: `${ANSWER} Gates control the cell.` } },
        { event: "citations", data: report },
        { event: "done", data: { answer: annotated } },
      ]),
    });
    renderWithStore(<Chat />);
    await ask("q?");
    expect(await screen.findByTestId("grounding")).toHaveTextContent("2 citations verified · 1 added automatically");
    const auto = screen.getByRole("button", { name: /Lecture 04 SVMs, page 3/ });
    expect(auto).toHaveClass("cite-inferred");
    expect(auto).toHaveTextContent("auto");
    expect(screen.getByRole("button", { name: /Lecture 04 SVMs, page 2/ })).not.toHaveClass("cite-inferred");
  });

  it("opens the corrected page for citations to the wrong page", async () => {
    const moved = { ...REPORT, page_mismatch_count: 1, grounded: false,
      citations: [{ ...REPORT.citations[0], page_supported: false, suggested_page: 3 }] };
    mockBackend({
      chat: streamOf([
        { event: "sources", data: { sources: [source] } },
        { event: "token", data: { text: ANSWER } },
        { event: "citations", data: moved },
        { event: "done", data: {} },
      ]),
    });
    renderWithStore(<><Chat /><ViewerProbe /></>);
    await ask("q?");
    expect(await screen.findByTestId("grounding")).toHaveTextContent("1 cited to the wrong page");
    const chip = screen.getByRole("button", { name: /Lecture 04 SVMs, page 2/ });
    expect(chip).toHaveClass("cite-moved");
    expect(chip).toHaveTextContent("p.2 → 3");
    fireEvent.click(chip);
    expect(screen.getByTestId("viewer-probe")).toHaveTextContent("d1:3:1");
  });

  it("shows the not-found state", async () => {
    mockBackend({
      chat: streamOf([
        { event: "sources", data: { sources: [] } },
        { event: "not_found", data: { message: "Not Found in Course Notes" } },
        { event: "done", data: { not_found: true } },
      ]),
    });
    renderWithStore(<Chat />);
    await ask("photosynthesis?");
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Not found in your course notes");
  });

  it("shows model errors inline", async () => {
    mockBackend({ chat: streamOf([{ event: "error", data: { message: "Cannot reach Ollama" } }]) });
    renderWithStore(<Chat />);
    await ask("hi there");
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach Ollama");
  });

  it("shows request failures (e.g. validation) inline", async () => {
    mockBackend({ chat: vi.fn().mockRejectedValue(new Error("question must not be blank")) });
    renderWithStore(<Chat />);
    await ask("x");
    expect(await screen.findByRole("alert")).toHaveTextContent("question must not be blank");
  });

  it("flags unverifiable citations", async () => {
    mockBackend({
      chat: streamOf([
        { event: "sources", data: { sources: [source] } },
        { event: "token", data: { text: "Claim [Source: Ghost.pdf, p. 9]." } },
        { event: "citations", data: { ...REPORT, citations: [], valid_count: 0, invalid_count: 1, grounded: false } },
        { event: "done", data: {} },
      ]),
    });
    renderWithStore(<Chat />);
    await ask("q?");
    expect(await screen.findByTestId("grounding")).toHaveTextContent("1 unverifiable citation");
    expect(screen.getByRole("button", { name: /Ghost, page 9/ })).toBeDisabled();
  });

  it("sends history, scope and deep search options", async () => {
    const chat = streamOf([{ event: "token", data: { text: "A [Source: Lecture_04_SVMs.pdf, p. 2]" } }, { event: "done", data: {} }]);
    mockBackend({ chat });
    renderWithStore(<Chat />);
    await ask("first question");
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByLabelText("Your question")).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /Deep search/ }));
    fireEvent.click(screen.getByRole("button", { name: /All documents/ }));
    fireEvent.click(screen.getByLabelText("Lecture 07 RNNs"));
    await ask("follow up");
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(2));
    expect(chat.mock.calls[1][0]).toMatchObject({
      expansion: "both",
      document_ids: ["d2"],
      history: [{ role: "user", content: "first question" }, { role: "assistant", content: "A [Source: Lecture_04_SVMs.pdf, p. 2]" }],
    });
  });

  it("does not send empty questions and offers suggestions", async () => {
    const chat = vi.fn();
    mockBackend({ chat });
    renderWithStore(<Chat />);
    expect(await screen.findByRole("button", { name: /Explain Kernels/ })).toBeInTheDocument();
    const box = screen.getByLabelText("Your question");
    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(chat).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
  });

  it("disables the composer when no documents are ready", async () => {
    mockBackend({ listDocuments: vi.fn().mockResolvedValue([]) });
    renderWithStore(<Chat />);
    expect(await screen.findByText(/Upload and process at least one document/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeDisabled();
  });

  it("persists history per course and restores interrupted messages as stopped", () => {
    localStorage.setItem("cnr.chat.c1", JSON.stringify([{ id: "1", role: "assistant", content: "x", stage: "generating" }]));
    expect(loadHistory("c1")[0].stage).toBe("stopped");
    localStorage.setItem("cnr.chat.c1", "{not json");
    expect(loadHistory("c1")).toEqual([]);
  });
});

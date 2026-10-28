import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mockBackend } from "../test/backend";
import { graded, question, renderWithStore, streamOf } from "../test/fixtures";
import { Quiz } from "./Quiz";

const quizEvents = (n: number) => [
  { event: "quiz", data: { quiz_id: "q1", topic: "KKT", total: n } },
  ...Array.from({ length: n }).flatMap((_, i) => [
    { event: "progress", data: { index: i + 1, total: n, section: "KKT", level: "Recall" } },
    { event: "question", data: { question: question(i + 1, { bloom_level: i % 2 ? "Conceptual" : "Recall" }) } },
  ]),
  { event: "done", data: { quiz_id: "q1", generated: n, requested: n } },
];

/** The button is disabled until the course's documents have loaded. */
async function clickGenerate() {
  const button = await screen.findByRole("button", { name: /Generate quiz/ });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}

async function generate() {
  await clickGenerate();
  return screen.findByTestId("question-card");
}

describe("Quiz", () => {
  beforeEach(() => localStorage.clear());

  it("sends the chosen settings", async () => {
    const quiz = streamOf(quizEvents(1));
    mockBackend({ quiz });
    renderWithStore(<Quiz />);
    fireEvent.change(await screen.findByLabelText("Quiz topic"), { target: { value: "KKT conditions" } });
    fireEvent.change(screen.getByLabelText("Number of questions"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Application" }));
    fireEvent.click(screen.getByRole("checkbox"));
    await generate();
    expect(quiz.mock.calls[0][0]).toEqual({
      course_id: "c1", topic: "KKT conditions", num_questions: 3, bloom_levels: ["Recall", "Conceptual"], adaptive: false,
    });
  });

  it("requires at least one Bloom level", async () => {
    mockBackend();
    renderWithStore(<Quiz />);
    for (const name of ["Recall", "Conceptual", "Application"]) {
      fireEvent.click(await screen.findByRole("button", { name }));
    }
    expect(screen.getByText("Pick at least one level.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate quiz/ })).toBeDisabled();
  });

  it("grades answers, explains every option and shows the mastery change", async () => {
    const answer = vi.fn().mockResolvedValue(graded(1, "A"));
    mockBackend({ quiz: streamOf(quizEvents(2)), answer });
    renderWithStore(<Quiz />);
    const card = await generate();
    expect(within(card).getByText("Question 1 of 2")).toBeInTheDocument();
    expect(card.querySelector(".q-stem .katex")).not.toBeNull();

    fireEvent.click(within(card).getByRole("button", { name: /Show hint/ }));
    expect(screen.getByRole("note")).toHaveTextContent("complementary slackness");

    fireEvent.click(screen.getByTestId("option-A"));
    fireEvent.click(screen.getByRole("button", { name: "Check answer" }));
    await waitFor(() => expect(answer).toHaveBeenCalledWith("q1", 1, "A"));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Not quite — the answer is B.");
    expect(screen.getByTestId("mastery-delta")).toHaveTextContent("Mastery 50% → 38%");
    expect(screen.getByText("Trap: sign / formula inversion")).toBeInTheDocument();
    expect(screen.getByText("Trap: boundary-condition violation")).toBeInTheDocument();
    expect(screen.getByText("Trap: terminology swap")).toBeInTheDocument();
    expect(screen.getByText("Matches the notes.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Lecture 04 SVMs · p. 2/ })).toBeInTheDocument();

    // Retry is allowed but does not move mastery.
    answer.mockResolvedValue(graded(1, "B", "B", { attempt_number: 2, first_try: false, mastery_updated: false, mastery_before: null, mastery_after: null }));
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    fireEvent.click(screen.getByTestId("option-B"));
    fireEvent.click(screen.getByRole("button", { name: "Check answer" }));
    expect(await screen.findByText("Retry · mastery counts first tries only")).toBeInTheDocument();
  });

  it("supports keyboard answering and finishes with a results summary", async () => {
    const answer = vi.fn().mockImplementation(async (_q: string, id: number, label: string) => graded(id, label));
    mockBackend({ quiz: streamOf(quizEvents(2)), answer });
    renderWithStore(<Quiz />);
    await generate();
    fireEvent.keyDown(window, { key: "2" });
    expect(screen.getByTestId("option-B")).toHaveClass("selected");
    fireEvent.keyDown(window, { key: "Enter" });
    await screen.findByText("Correct!");
    fireEvent.keyDown(window, { key: "Enter" });
    expect(await screen.findByText("Question 2 of 2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "c" });
    fireEvent.keyDown(window, { key: "Enter" });
    await screen.findByText(/Not quite/);
    fireEvent.click(screen.getByRole("button", { name: /See results/ }));
    const results = await screen.findByTestId("results");
    expect(results).toHaveTextContent("1/2");
    expect(results).toHaveTextContent("Good progress");
    expect(within(results).getByText("Recall")).toBeInTheDocument();
  });

  it("shows generation progress and server errors", async () => {
    mockBackend({
      quiz: streamOf([
        { event: "quiz", data: { quiz_id: "q1", topic: "x", total: 2 } },
        { event: "warning", data: { message: "Skipped question 1" } },
        { event: "error", data: { message: "Cannot reach Ollama" } },
        { event: "done", data: { generated: 0 } },
      ]),
    });
    renderWithStore(<Quiz />);
    await clickGenerate();
    expect(await screen.findByText("No questions could be generated")).toBeInTheDocument();
    expect(screen.getByText("Cannot reach Ollama")).toBeInTheDocument();
    expect(screen.getByText("Skipped question 1")).toBeInTheDocument();
  });

  it("returns to setup when the request is rejected", async () => {
    mockBackend({ quiz: vi.fn().mockRejectedValue(new Error('"castles" was not found in your course notes')) });
    renderWithStore(<Quiz />);
    await clickGenerate();
    expect(await screen.findByText(/was not found in your course notes/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate quiz/ })).toBeInTheDocument();
  });

  it("lists weak spots sorted by mastery and practises a section", async () => {
    const quiz = streamOf(quizEvents(1));
    mockBackend({ quiz });
    renderWithStore(<Quiz />);
    const rows = await screen.findAllByTestId("mastery-row");
    expect(rows[0]).toHaveTextContent("4 Kernels");
    expect(rows[0]).toHaveTextContent("38%");
    expect(rows[1]).toHaveTextContent("not practised");
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Practise" }));
    await screen.findByTestId("question-card");
    expect(quiz.mock.calls[0][0]).toMatchObject({ topic: "4 Kernels", adaptive: false });
  });
});

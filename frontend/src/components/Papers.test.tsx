import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  papersApi, type Blueprint, type MockQuestion, type PaperDetail, type PaperInfo, type PastQuestion, type PlanItem,
  type RevisionPlan,
} from "../papersApi";
import { useStore } from "../store";
import { mockBackend } from "../test/backend";
import { doc, renderWithStore, streamOf } from "../test/fixtures";
import { Papers } from "./Papers";
import { BarList, ColumnChart, marks, pct, YearChips } from "./papers/charts";

const paper = (over: Partial<PaperInfo> = {}): PaperInfo => ({
  id: "p1", course_id: "c1", pdf_name: "End_Semester_2024.pdf", size_bytes: 1000, title: "End-Semester Nov/Dec 2024",
  exam_type: "End-Semester", year: 2024, term: "Nov/Dec 2024", max_marks: 40, duration: "3 Hours", parts: [], warnings: [],
  status: "ready", progress: 1, error: null, engine: "docling", num_pages: 2, question_count: 11, marks_found: 70,
  created_at: "2026-10-28", updated_at: "2026-10-28", ...over,
});

const past = (over: Partial<PastQuestion> = {}): PastQuestion => ({
  question_id: "q1", paper_id: "p1", paper_title: "End-Semester 2024", year: 2024, label: "7(a)", part: "B",
  text: "Calculate $P(D | T)$ using Bayes' theorem.", context: "", marks: 10, page: 1, ...over,
});

const section = {
  module: "Lecture 4", document_id: "d1", notes_pdf: "Lecture_04_SVMs.pdf", mapped: true, marks: 34, share: 0.5,
  marks_per_paper: 8.5, expected_marks: 8.8, questions: 6, papers: 2, frequency: 1, years: [2023, 2024],
  types: { derivation: 20, definition: 14 }, parent_id: "s1", section_title: "3 The Optimal Margin Classifier",
  page_start: 2, page_end: 3, examples: [past()],
};

const blueprint: Blueprint = {
  summary: { papers: 2, questions: 22, years: [2023, 2024], marks: 80, marks_per_paper: 40, coverage: 0.9, numericals: 3, unmarked: 0 },
  modules: [
    { ...section, section_title: undefined } as never,
    { ...section, module: "Not in your notes", notes_pdf: null, document_id: null, mapped: false, marks_per_paper: 2, share: 0.05 } as never,
  ],
  sections: [section],
  types: [
    { key: "definition", label: "Definition / short theory", count: 10, marks: 20, share: 0.25 },
    { key: "numerical", label: "Numerical / problem solving", count: 4, marks: 40, share: 0.5 },
    { key: "diagram", label: "Diagram", count: 0, marks: 0, share: 0 },
  ],
  bloom: [{ key: "Remember", count: 10, marks: 20, share: 0.25 }, { key: "Apply", count: 6, marks: 60, share: 0.75 }],
  marks_distribution: [{ marks: 2, count: 10 }, { marks: 10, count: 12 }],
  structures: [
    {
      paper_id: "p1", title: "End-Semester Nov/Dec 2024", exam_type: "End-Semester", year: 2024, max_marks: 40, duration: "3 Hours",
      total_marks: 40, signature: "x",
      parts: [
        { name: "A", instructions: "Answer ALL questions", questions: 5, required: 5, marks_each: 2, total: 10, either_or: false, choice: false, pattern: "5 × 2" },
        { name: "B", instructions: "", questions: 3, required: 3, marks_each: 10, total: 30, either_or: true, choice: false, pattern: "3 × 10" },
      ],
    },
  ],
  typical: null,
  recurring: [{
    text: "State Bayes' theorem.", section_title: "Bayes' Theorem", parent_id: "s2", question_type: "definition", marks: 2, papers: 2,
    years: [2023, 2024], instances: [past()],
  }],
  phrasings: [{ phrasing: "Explain … with a neat diagram", count: 3, question_type: "diagram", typical_marks: 10, examples: [past({ text: "Explain with a neat diagram the LSTM cell." })] }],
  practice_mix: [{ mode: "numericals", label: "Numericals", marks: 40, share: 0.5 }, { mode: "theory", label: "Theory", marks: 40, share: 0.5 }],
  gaps: [], stale_questions: 0,
};
blueprint.typical = { ...blueprint.structures[0], paper_count: 2 };

const item = (over: Partial<PlanItem> = {}): PlanItem => ({
  rank: 1, priority: 100, tier: "must", tier_label: "Must revise", parent_id: "s1", section_title: "3.2 KKT Conditions", module: "Lecture 4",
  notes_pdf: "Lecture_04_SVMs.pdf", document_id: "d1", page_start: 2, page_end: 3, marks_per_paper: 8.5, expected_marks: 8.8, frequency: 1,
  papers: 2, years: [2023, 2024], examples: [past()], mastery: 0.3, mastery_attempts: 2, focus: "derivations", focus_label: "Derivations",
  practice: [{ mode: "derivations", label: "Derivations", share: 0.6 }, { mode: "theory", label: "Theory", share: 0.4 }],
  advice: "Asked in 2 of 2 papers, about 8.5 marks a paper. Write the derivation end to end.", ...over,
});

const plan: RevisionPlan = {
  summary: blueprint.summary, practice_mix: blueprint.practice_mix, gaps: [], stale_questions: 0, schedule: [], days: null,
  items: [item(), item({ rank: 2, tier: "optional", tier_label: "If time permits", section_title: "4 Kernels", priority: 40, mastery_attempts: 0 })],
};

const mockQuestion = (over: Partial<MockQuestion> = {}): MockQuestion => ({
  ord: 1, label: "1", number: "1", sub: "", part: "A", choice_group: "", marks: 2, question_type: "numerical",
  type_label: "Numerical / problem solving", bloom_level: "Apply", question: "Compute $\\gamma$ for $w = 3$.",
  solution: "1. The margin is the ratio. [Source: Lecture_04_SVMs.pdf, p. 2]\n\n2. So it is 2.", final_answer: "The margin is 2.",
  answer_check: { expression: "6 / 3", value: 2 }, marking: [{ point: "Formula", marks: 1 }, { point: "Result", marks: 1 }],
  report: {
    citations: [{
      raw: "[Source: Lecture_04_SVMs.pdf, p. 2]", pdf_name: "Lecture_04_SVMs.pdf", page: 2, page_end: 2, start: 0, end: 1, valid: true,
      reason: null, document_id: "d1", resolved_pdf: "Lecture_04_SVMs.pdf", bboxes: [{ page: 2, x0: 1, y0: 2, x1: 3, y1: 4 }],
    }],
    valid_count: 1, invalid_count: 0, uncited_sentences: [], claim_count: 1, coverage: 1, grounded: true,
  },
  section_title: "2.2 Geometric Margin", lecture_title: "Lecture 4", parent_id: "s1", document_id: "d1", pdf_name: "Lecture_04_SVMs.pdf",
  evidence: null, ...over,
});

const detail: PaperDetail = {
  ...paper(),
  questions: [{
    id: "q1", number: "1", sub: "", label: "1", part: "A", choice_group: "", context: "", text: "State Bayes' theorem.", marks: 2,
    marks_estimated: false, page: 1, question_type: "definition", bloom_level: "Remember", section_title: null, lecture_title: null, topic_score: null,
  }],
};

function mockPapers(over: Partial<Record<keyof typeof papersApi, unknown>> = {}) {
  const defaults = {
    list: vi.fn().mockResolvedValue([paper()]),
    upload: vi.fn().mockResolvedValue({ papers: [paper()], errors: [] }),
    addSamples: vi.fn().mockResolvedValue({ papers: [paper()], skipped: [] }),
    remap: vi.fn().mockResolvedValue({ queued: 1 }),
    blueprint: vi.fn().mockResolvedValue(blueprint),
    plan: vi.fn().mockResolvedValue(plan),
    get: vi.fn().mockResolvedValue(detail),
    update: vi.fn().mockResolvedValue(paper()),
    remove: vi.fn().mockResolvedValue(undefined),
    reprocess: vi.fn().mockResolvedValue(paper({ status: "queued" })),
    listMocks: vi.fn().mockResolvedValue([]),
    getMock: vi.fn(),
    removeMock: vi.fn().mockResolvedValue(undefined),
    mock: vi.fn(),
  };
  const mocks = { ...defaults, ...over } as Record<string, ReturnType<typeof vi.fn>>;
  for (const [key, fn] of Object.entries(mocks)) {
    vi.spyOn(papersApi, key as "list").mockImplementation(fn as never);
  }
  return mocks;
}

const showView = async (name: string) => fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${name}`) }));

function ViewerProbe() {
  const { viewer } = useStore();
  return viewer ? <div data-testid="viewer-target">{`${viewer.pdfName}:${viewer.page}:${viewer.bboxes.length}`}</div> : null;
}

const paperEvents = [
  { event: "mock", data: { mock_id: "m1", title: "Mock paper in the style of FAT", kind: "paper", total: 3, spec: { kind: "paper", max_marks: 12, duration: "3 Hours", parts: blueprint.structures[0].parts } } },
  { event: "progress", data: { index: 1, total: 3, label: "1", section: "Margins", question_type: "Numerical" } },
  { event: "question", data: { question: mockQuestion() } },
  { event: "question", data: { question: mockQuestion({ ord: 2, label: "6(a)", number: "6", sub: "a", part: "B", choice_group: "6", marks: 10, question: "Derive the dual." }) } },
  { event: "question", data: { question: mockQuestion({ ord: 3, label: "6(b)", number: "6", sub: "b", part: "B", choice_group: "6", marks: 10, question: "Explain the KKT conditions." }) } },
  { event: "done", data: { mock_id: "m1", generated: 3, requested: 3 } },
];

describe("Papers", () => {
  beforeEach(() => {
    localStorage.clear();
    mockBackend();
  });

  it("opens on the papers list when nothing is uploaded yet, and offers the samples", async () => {
    const m = mockPapers({ list: vi.fn().mockResolvedValue([]) });
    renderWithStore(<Papers pollMs={10} />);
    fireEvent.click(await screen.findByRole("button", { name: /Load sample papers/ }));
    await waitFor(() => expect(m.addSamples).toHaveBeenCalledWith("c1"));
    expect(await screen.findByText(/Reading 1 sample papers/)).toBeInTheDocument();
    expect(m.blueprint).not.toHaveBeenCalled();
  });

  it("explains an empty blueprint instead of failing", async () => {
    mockPapers({ list: vi.fn().mockResolvedValue([]) });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Blueprint");
    expect(await screen.findByRole("heading", { name: "Start with a past paper" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add papers" }));
    expect(await screen.findByTestId("paper-dropzone")).toBeInTheDocument();
  });

  it("opens on the blueprint once a paper is ready", async () => {
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    expect(await screen.findByText("Papers analysed")).toBeInTheDocument();
  });

  it("uploads valid files and refuses others before sending them", async () => {
    const m = mockPapers({ list: vi.fn().mockResolvedValue([]) });
    renderWithStore(<Papers pollMs={10} />);
    const input = await screen.findByTestId("paper-input");
    const good = new File(["%PDF-"], "paper.pdf", { type: "application/pdf" });
    const bad = new File(["x"], "notes.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [good, bad] } });
    await waitFor(() => expect(m.upload).toHaveBeenCalled());
    expect(m.upload.mock.calls[0][1]).toEqual([good]);
    expect(await screen.findByText("notes.txt is not a PDF")).toBeInTheDocument();
  });

  it("reports upload errors from the server", async () => {
    mockPapers({
      list: vi.fn().mockResolvedValue([]),
      upload: vi.fn().mockRejectedValue(new Error("paper.pdf is already in this course")),
    });
    renderWithStore(<Papers pollMs={10} />);
    fireEvent.change(await screen.findByTestId("paper-input"), {
      target: { files: [new File(["%PDF-"], "paper.pdf", { type: "application/pdf" })] },
    });
    expect(await screen.findByText(/already in this course/)).toBeInTheDocument();
  });

  it("follows a paper through processing", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce([paper({ status: "classifying", progress: 0.6 })])
      .mockResolvedValue([paper()]);
    mockPapers({ list });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Papers");
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1));
    expect(await screen.findByText("Ready for the blueprint and mock papers")).toBeInTheDocument();
  });

  it("stays on the papers list while an upload finishes", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([paper({ status: "parsing", progress: 0.1 })])
      .mockResolvedValue([paper()]);
    mockPapers({ list });
    renderWithStore(<Papers pollMs={10} />);
    fireEvent.change(await screen.findByTestId("paper-input"), {
      target: { files: [new File(["%PDF-"], "paper.pdf", { type: "application/pdf" })] },
    });
    expect(await screen.findByText("Ready for the blueprint and mock papers")).toBeInTheDocument();
    expect(screen.getByTestId("paper-dropzone")).toBeInTheDocument();
    expect(screen.queryByText("Papers analysed")).not.toBeInTheDocument();
  });

  it("shows why a paper failed and lets it be retried", async () => {
    const m = mockPapers({ list: vi.fn().mockResolvedValue([paper({ status: "failed", error: "No exam questions were found in this PDF" })]) });
    renderWithStore(<Papers pollMs={10} />);
    expect(await screen.findByText(/No exam questions were found/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Reprocess/ }));
    await waitFor(() => expect(m.reprocess).toHaveBeenCalledWith("p1"));
  });

  it("corrects a paper's title and year", async () => {
    const m = mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Papers");
    fireEvent.click(await screen.findByRole("button", { name: /^Edit/ }));
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "FAT 2024" } });
    fireEvent.change(screen.getByLabelText("Paper year"), { target: { value: "2023" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(m.update).toHaveBeenCalledWith("p1", { title: "FAT 2024", year: 2023 }));
  });

  it("will not save an impossible year", async () => {
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Papers");
    fireEvent.click(await screen.findByRole("button", { name: /^Edit/ }));
    fireEvent.change(screen.getByLabelText("Paper year"), { target: { value: "20" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("asks before deleting a paper", async () => {
    const m = mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Papers");
    fireEvent.click(await screen.findByRole("button", { name: /^Delete End_Semester/ }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete paper" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep" }));
    expect(m.remove).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: /^Delete End_Semester/ }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(m.remove).toHaveBeenCalledWith("p1"));
  });

  it("lists a paper's questions so they can be checked against the PDF", async () => {
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Papers");
    fireEvent.click(await screen.findByRole("button", { name: /^See questions/ }));
    const drawer = await screen.findByRole("dialog", { name: "Extracted questions" });
    expect(within(drawer).getByTestId("question-row")).toHaveTextContent("State Bayes' theorem.");
    expect(within(drawer).getByText("Not in your notes")).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Open the PDF" })).toHaveAttribute("href", "/api/papers/p1/file");
  });
});

describe("Blueprint", () => {
  beforeEach(() => {
    localStorage.clear();
    mockBackend();
  });

  it("shows the structure, weightage, type mix, recurring questions and phrasing", async () => {
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    expect(await screen.findByTestId("typical-structure")).toHaveTextContent("5 × 2");
    expect(screen.getByTestId("typical-structure")).toHaveTextContent("either/or choices");
    expect(screen.getByText("Covered by your notes").parentElement).toHaveTextContent("90%");
    expect(screen.getByLabelText("Marks by lecture")).toHaveTextContent("Lecture 4");
    expect(screen.getByTestId("section-row")).toHaveTextContent("2 of 2");
    expect(screen.getByTestId("section-row")).toHaveTextContent("Derivation");
    const types = screen.getByLabelText("Marks by question type");
    expect(types).toHaveTextContent("Numerical / problem solving");
    expect(types).not.toHaveTextContent("Diagram");
    expect(within(types).getAllByRole("listitem")[0]).toHaveTextContent("Numerical");
    expect(screen.getByTestId("recurring")).toHaveTextContent("State Bayes' theorem.");
    expect(screen.getByTestId("recurring")).toHaveTextContent("2 papers");
    expect(screen.getByTestId("phrasing")).toHaveTextContent("Explain … with a neat diagram");
    expect(screen.getByTestId("phrasing")).toHaveTextContent("End-Semester 2024 · Q7(a) · 10 marks");
  });

  it("opens the notes behind a section", async () => {
    mockPapers();
    renderWithStore(<><Papers pollMs={10} /><ViewerProbe /></>);
    fireEvent.click(await screen.findByRole("button", { name: /3 The Optimal Margin Classifier/ }));
    expect(screen.getByTestId("viewer-target")).toHaveTextContent("Lecture_04_SVMs.pdf:2:0");
  });

  it("offers to match again when the notes have changed", async () => {
    const m = mockPapers({ blueprint: vi.fn().mockResolvedValue({ ...blueprint, stale_questions: 3 }) });
    renderWithStore(<Papers pollMs={10} />);
    expect(await screen.findByText(/3 questions point to sections/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Match again/ }));
    await waitFor(() => expect(m.remap).toHaveBeenCalledWith("c1"));
  });

  it("lists questions the notes do not cover", async () => {
    mockPapers({ blueprint: vi.fn().mockResolvedValue({ ...blueprint, gaps: [past({ text: "Explain photosynthesis." })] }) });
    renderWithStore(<Papers pollMs={10} />);
    const gaps = await screen.findByRole("region", { name: "Gaps in your notes" });
    expect(gaps).toHaveTextContent("Explain photosynthesis.");
  });

  it("says when nothing repeats yet", async () => {
    mockPapers({ blueprint: vi.fn().mockResolvedValue({ ...blueprint, recurring: [] }) });
    renderWithStore(<Papers pollMs={10} />);
    expect(await screen.findByText(/Nothing repeats yet/)).toBeInTheDocument();
  });

  it("reports a failed load", async () => {
    mockPapers({ blueprint: vi.fn().mockRejectedValue(new Error("server down")) });
    renderWithStore(<Papers pollMs={10} />);
    expect(await screen.findByText("server down")).toBeInTheDocument();
  });
});

describe("Revision plan", () => {
  beforeEach(() => {
    localStorage.clear();
    mockBackend();
  });

  it("groups sections by tier and says how to practise each", async () => {
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Revision plan");
    const must = await screen.findByRole("region", { name: "Must revise" });
    expect(within(must).getByTestId("plan-item")).toHaveTextContent("3.2 KKT Conditions");
    expect(within(must).getByTestId("plan-item")).toHaveTextContent("Derivations");
    expect(within(must).getByTestId("plan-item")).toHaveTextContent("Write the derivation end to end.");
    expect(within(must).getByTestId("plan-item")).toHaveTextContent("30%");
    expect(screen.getByRole("region", { name: "If time permits" })).toHaveTextContent("Not practised");
    expect(screen.getByLabelText("Marks by kind of practice")).toHaveTextContent("Numericals");
  });

  it("spreads the plan over the days left", async () => {
    const withDays = { ...plan, days: 3, schedule: [
      { day: 1, title: "Day 1", sections: ["3.2 KKT Conditions"], focus: ["Derivations"], expected_marks: 8 },
      { day: 2, title: "Timed mock paper", sections: [], focus: ["Sit a mock paper under exam conditions"], expected_marks: 0 },
    ] };
    const m = mockPapers({ plan: vi.fn().mockImplementation((_c, days) => Promise.resolve(days ? withDays : plan)) });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Revision plan");
    fireEvent.change(await screen.findByLabelText("Days until the exam"), { target: { value: "3" } });
    const schedule = await screen.findByLabelText("Daily schedule");
    expect(schedule).toHaveTextContent("Timed mock paper");
    expect(m.plan).toHaveBeenLastCalledWith("c1", 3);
  });

  it("refuses a number of days the server would reject", async () => {
    const m = mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Revision plan");
    fireEvent.change(await screen.findByLabelText("Days until the exam"), { target: { value: "90" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number from 1 to 60.");
    expect(m.plan).not.toHaveBeenCalledWith("c1", 90);
  });

  it("shows real past questions on request and opens the notes", async () => {
    mockPapers();
    renderWithStore(<><Papers pollMs={10} /><ViewerProbe /></>);
    await showView("Revision plan");
    const card = within((await screen.findAllByTestId("plan-item"))[0]);
    fireEvent.click(card.getByRole("button", { name: "Past questions (1)" }));
    expect(card.getByText("End-Semester 2024 · Q7(a) · 10 marks")).toBeInTheDocument();
    fireEvent.click(card.getByRole("button", { name: /Lecture 04 SVMs · p. 2/ }));
    expect(screen.getByTestId("viewer-target")).toHaveTextContent("Lecture_04_SVMs.pdf:2:0");
  });

  it("sends a section to the practice generator", async () => {
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Revision plan");
    fireEvent.click((await screen.findAllByRole("button", { name: /Practise this/ }))[0]);
    expect(await screen.findByLabelText("Practice topic")).toHaveValue("3.2 KKT Conditions");
    expect(screen.getByLabelText("Number of practice questions")).toHaveValue("3");
  });

  it("explains an empty plan", async () => {
    mockPapers({ plan: vi.fn().mockResolvedValue({ ...plan, items: [], gaps: [past()] }) });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Revision plan");
    expect(await screen.findByText(/None of the questions matched your notes/)).toBeInTheDocument();
    expect(screen.getByText(/1 past question is on topics missing/)).toBeInTheDocument();
  });
});

describe("Mock paper", () => {
  beforeEach(() => {
    localStorage.clear();
    mockBackend();
  });

  const generate = async () => {
    await showView("Mock paper");
    const button = await screen.findByRole("button", { name: "Generate" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
  };

  it("generates a full paper in the chosen pattern", async () => {
    const mock = streamOf(paperEvents);
    mockPapers({ mock });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Mock paper");
    fireEvent.change(await screen.findByLabelText("Template paper"), { target: { value: "p1" } });
    await generate();
    expect(await screen.findByRole("heading", { name: "Mock paper in the style of FAT" })).toBeInTheDocument();
    expect(await screen.findAllByTestId("mock-question")).toHaveLength(3);
    expect(mock.mock.calls[0][0]).toEqual({ course_id: "c1", kind: "paper", template_paper_id: "p1" });
    expect(screen.getByRole("region", { name: "Part A" })).toHaveTextContent("5 × 2 = 10 marks");
    expect(screen.getByRole("region", { name: "Part A" })).toHaveTextContent("Answer ALL questions");
    expect(screen.getByTestId("mock-sheet")).toHaveTextContent("12 marks · 3 Hours");
  });

  it("marks the choice between the two sides of an OR, and only there", async () => {
    mockPapers({ mock: streamOf(paperEvents) });
    renderWithStore(<Papers pollMs={10} />);
    await generate();
    expect(await screen.findByText("OR")).toBeInTheDocument();
    expect(screen.getAllByText("OR")).toHaveLength(1);
  });

  it("keeps solutions hidden until asked, then shows steps, citations and the answer check", async () => {
    mockPapers({ mock: streamOf(paperEvents) });
    renderWithStore(<><Papers pollMs={10} /><ViewerProbe /></>);
    await generate();
    const first = (await screen.findAllByTestId("mock-question"))[0];
    expect(screen.queryByTestId("solution")).not.toBeInTheDocument();
    fireEvent.click(within(first).getByRole("button", { name: /Show worked solution/ }));
    const solution = within(first).getByTestId("solution");
    expect(solution).toHaveTextContent("The margin is the ratio.");
    expect(within(first).getByTestId("answer-check")).toHaveTextContent("6 / 3 = 2");
    expect(solution).toHaveTextContent("Formula");
    fireEvent.click(within(solution).getByRole("button", { name: /Open citation: Lecture 04 SVMs, page 2/ }));
    expect(screen.getByTestId("viewer-target")).toHaveTextContent("Lecture_04_SVMs.pdf:2:1");
  });

  it("shows all solutions at once", async () => {
    mockPapers({ mock: streamOf(paperEvents) });
    renderWithStore(<Papers pollMs={10} />);
    await generate();
    await screen.findAllByTestId("mock-question");
    fireEvent.click(screen.getByRole("button", { name: "Show all solutions" }));
    expect(await screen.findAllByTestId("solution")).toHaveLength(3);
  });

  it("does not claim an arithmetic check for a theory question", async () => {
    const events = [paperEvents[0], { event: "question", data: { question: mockQuestion({ answer_check: null, question_type: "definition" }) } }, paperEvents[5]];
    mockPapers({ mock: streamOf(events) });
    renderWithStore(<Papers pollMs={10} />);
    await generate();
    fireEvent.click(await screen.findByRole("button", { name: /Show worked solution/ }));
    expect(screen.queryByTestId("answer-check")).not.toBeInTheDocument();
  });

  it("sends the practice set's settings", async () => {
    const mock = streamOf(paperEvents);
    mockPapers({ mock });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Mock paper");
    fireEvent.click(await screen.findByRole("button", { name: "Practice set" }));
    fireEvent.change(screen.getByLabelText("Practice topic"), { target: { value: " KKT " } });
    fireEvent.change(screen.getByLabelText("Number of practice questions"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Numerical" }));
    fireEvent.click(screen.getByRole("button", { name: "Derivation" }));
    fireEvent.click(screen.getByRole("button", { name: "Derivation" }));
    await generate();
    await screen.findAllByTestId("mock-question");
    expect(mock.mock.calls[0][0]).toEqual({
      course_id: "c1", kind: "practice", num_questions: 4, question_types: ["numerical"], topic: "KKT",
    });
  });

  it("shows warnings for skipped questions and errors from the model", async () => {
    const events = [
      paperEvents[0], paperEvents[2],
      { event: "warning", data: { message: "Skipped question 2: the model could not write it." } },
      { event: "error", data: { message: "Ollama is not running" } },
      paperEvents[5],
    ];
    mockPapers({ mock: streamOf(events) });
    renderWithStore(<Papers pollMs={10} />);
    await generate();
    expect(await screen.findByText(/Skipped question 2/)).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Ollama is not running");
    expect(screen.getAllByTestId("mock-question")).toHaveLength(1);
  });

  it("returns to the form when the request is refused", async () => {
    mockPapers({ mock: vi.fn().mockRejectedValue(new Error('"Photosynthesis" was not found in your course notes.')) });
    renderWithStore(<Papers pollMs={10} />);
    await generate();
    expect(await screen.findByText(/was not found in your course notes/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Generate" })).toBeInTheDocument();
  });

  it("needs notes before it can generate anything", async () => {
    mockBackend({ listDocuments: vi.fn().mockResolvedValue([doc({ status: "parsing" })]) });
    mockPapers();
    renderWithStore(<Papers pollMs={10} />);
    await showView("Mock paper");
    expect(await screen.findByRole("button", { name: "Generate" })).toBeDisabled();
    expect(screen.getByText("Upload and process notes in the Library first.")).toBeInTheDocument();
  });

  it("reopens and deletes saved papers", async () => {
    const saved = { id: "m9", kind: "paper", title: "Mock from yesterday", created_at: "2026-10-28", question_count: 1 };
    const m = mockPapers({
      listMocks: vi.fn().mockResolvedValue([saved]),
      getMock: vi.fn().mockResolvedValue({ ...saved, course_id: "c1", spec: { kind: "paper", max_marks: 2 }, questions: [mockQuestion()] }),
    });
    renderWithStore(<Papers pollMs={10} />);
    await showView("Mock paper");
    fireEvent.click(await screen.findByRole("button", { name: "Delete Mock from yesterday" }));
    await waitFor(() => expect(m.removeMock).toHaveBeenCalledWith("m9"));
    fireEvent.click(await screen.findByRole("button", { name: "Mock from yesterday" }));
    expect(await screen.findByTestId("mock-question")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    expect(await screen.findByRole("button", { name: "Generate" })).toBeInTheDocument();
  });
});

describe("charts", () => {
  it("scales bars to the largest value and prints each value", () => {
    renderWithStore(<BarList label="Bars" bars={[
      { key: "a", label: "A", value: 10, display: "10" }, { key: "b", label: "B", value: 5, display: "5" },
    ]} />);
    const rows = within(screen.getByLabelText("Bars")).getAllByRole("listitem");
    expect(rows[1]).toHaveTextContent("5");
    expect(rows[1].querySelector<HTMLElement>(".bar-fill")?.style.width).toBe("50%");
  });

  it("copes with all-zero bars and no bars", () => {
    renderWithStore(<BarList label="Zero" bars={[{ key: "a", label: "A", value: 0, display: "0" }]} />);
    expect(screen.getByLabelText("Zero").querySelector<HTMLElement>(".bar-fill")?.style.width).toBe("0px");
    renderWithStore(<BarList label="None" bars={[]} />);
    expect(within(screen.getByLabelText("None")).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("draws columns with their counts", () => {
    renderWithStore(<ColumnChart label="Cols" columns={[{ key: "2", value: 4, caption: "2m" }, { key: "10", value: 8, caption: "10m" }]} />);
    const bars = screen.getByLabelText("Cols").querySelectorAll<HTMLElement>(".column-bar");
    expect(bars[0].style.height).toBe("50%");
    expect(screen.getByLabelText("Cols")).toHaveTextContent("10m");
  });

  it("formats marks and percentages", () => {
    expect(marks(2.5)).toBe("2.5");
    expect(marks(10)).toBe("10");
    expect(marks(null)).toBe("–");
    expect(pct(0.256)).toBe("26%");
  });

  it("hides the year strip when there are no years", () => {
    const { container } = renderWithStore(<YearChips years={[]} />);
    expect(container.querySelector(".year-chips")).toBeNull();
  });
});

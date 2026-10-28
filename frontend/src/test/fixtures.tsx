import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import type { AnswerResult, Course, DocumentInfo, Health, MasterySummary, PublicQuestion, Source } from "../api";
import type { SSEEvent } from "../sse";
import { Toasts } from "../components/Toasts";
import { StoreProvider } from "../store";

export const course: Course = {
  id: "c1", name: "Machine Learning", code: "CS229", created_at: "2026-10-28", document_count: 2, ready_count: 2,
};

export const doc = (over: Partial<DocumentInfo> = {}): DocumentInfo => ({
  id: "d1", course_id: "c1", pdf_name: "Lecture_04_SVMs.pdf", status: "ready", progress: 1, error: null,
  num_pages: 3, page_sizes: [[595, 842], [595, 842], [595, 842]], is_slides: false, engine: "docling",
  num_parents: 2, num_children: 7, size_bytes: 52000, created_at: "2026-10-28", ...over,
});

export const health: Health = {
  status: "ok", profile: "test",
  llm: { ok: true, provider: "ollama", model: "qwen2.5:7b", detail: "ready" },
  models: { embedding: "BAAI/bge-m3", reranker: "BAAI/bge-reranker-v2-m3", llm: "qwen2.5:7b", parser: "docling" },
};

export const source: Source = {
  parent_id: "p1", document_id: "d1", pdf_name: "Lecture_04_SVMs.pdf", lecture_title: "Lecture 4",
  section_title: "3.2 KKT Conditions", page_start: 2, page_end: 3, rrf_score: 0.03, rerank_score: 0.91,
  excerpt: "Complementary slackness requires…", highlight_bboxes: [{ page: 2, x0: 50, y0: 100, x1: 500, y1: 140 }],
};

export const mastery: MasterySummary = {
  course_id: "c1", overall: 0.48, attempted_sections: 1, total_sections: 2,
  sections: [
    { parent_id: "p2", section_title: "4 Kernels", lecture_title: "Lecture 4", pdf_name: "Lecture_04_SVMs.pdf",
      document_id: "d1", page_start: 3, page_end: 3, score: 0.375, attempts: 1, correct: 0 },
    { parent_id: "p1", section_title: "3.2 KKT Conditions", lecture_title: "Lecture 4", pdf_name: "Lecture_04_SVMs.pdf",
      document_id: "d1", page_start: 2, page_end: 2, score: 0.5, attempts: 0, correct: 0 },
  ],
};

export const question = (id: number, over: Partial<PublicQuestion> = {}): PublicQuestion => ({
  question_id: id, bloom_level: "Recall", question_stem: `Question ${id}: what is $\\alpha_i$?`,
  options: ["A", "B", "C", "D"].map((label) => ({ label, text: `Option ${label}` })),
  hint: "Think about complementary slackness.", source_pdf: "Lecture_04_SVMs.pdf", source_page: 2,
  section_title: "KKT Conditions", document_id: "d1", ...over,
});

export const graded = (qid: number, selected: string, correct = "B", over: Partial<AnswerResult> = {}): AnswerResult => ({
  quiz_id: "q1", question_id: qid, selected_label: selected, is_correct: selected === correct, correct_label: correct,
  attempt_number: 1, first_try: true, mastery_updated: true, mastery_before: 0.5,
  mastery_after: selected === correct ? 0.625 : 0.375,
  options: ["A", "B", "C", "D"].map((label) => ({
    label, text: `Option ${label}`, is_correct: label === correct,
    trap_type: label === correct ? "correct" : label === "A" ? "sign_inversion" : label === "C" ? "boundary_violation" : "terminology_swap",
    misconception_explanation: label === correct ? "Matches the notes." : `Misconception ${label}.`,
  })),
  source_quote: "Complementary slackness requires the product to vanish.", source_pdf: "Lecture_04_SVMs.pdf",
  source_page: 2, source_bboxes: [{ page: 2, x0: 1, y0: 2, x1: 3, y1: 4 }], document_id: "d1",
  section_title: "KKT Conditions", ...over,
});

/** Makes a fake streaming endpoint that replays the given events. */
export function streamOf(events: SSEEvent[], error?: Error) {
  return vi.fn(async (_body: unknown, onEvent: (e: SSEEvent) => void) => {
    for (const e of events) {
      await Promise.resolve();
      onEvent(e);
    }
    if (error) throw error;
  });
}

/** Renders inside the store; adds the toast list unless the UI (e.g. <App/>) has its own. */
export function renderWithStore(ui: ReactNode, { toasts = true }: { toasts?: boolean } = {}) {
  return render(
    <StoreProvider pollMs={20}>
      {ui}
      {toasts && <Toasts />}
    </StoreProvider>,
  );
}

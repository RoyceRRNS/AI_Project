import { ApiError, errorFromResponse, streamSSE, type SSEEvent } from "./sse";

export { ApiError };

export type DocStatus = "queued" | "parsing" | "chunking" | "indexing" | "ready" | "failed";
export type BloomLevel = "Recall" | "Conceptual" | "Application";
export type TrapType = "correct" | "sign_inversion" | "boundary_violation" | "terminology_swap";
export type Expansion = "none" | "multi_query" | "hyde" | "both";

export interface BBox { page: number; x0: number; y0: number; x1: number; y1: number }

export interface Course {
  id: string;
  name: string;
  code: string | null;
  created_at: string;
  document_count: number;
  ready_count: number;
}

export interface DocumentInfo {
  id: string;
  course_id: string;
  pdf_name: string;
  status: DocStatus;
  progress: number;
  error: string | null;
  num_pages: number | null;
  page_sizes: [number, number][] | null;
  is_slides: boolean;
  engine: string | null;
  num_parents: number;
  num_children: number;
  size_bytes: number;
  created_at: string;
}

export interface UploadResult {
  documents: DocumentInfo[];
  errors: { filename: string; detail: string; status: number }[];
}

export interface Source {
  parent_id: string;
  document_id: string;
  pdf_name: string;
  lecture_title: string;
  section_title: string;
  page_start: number;
  page_end: number;
  rrf_score: number;
  rerank_score: number;
  excerpt: string;
  highlight_bboxes: BBox[];
}

export interface Citation {
  raw: string;
  pdf_name: string;
  page: number;
  page_end: number;
  start: number;
  end: number;
  valid: boolean;
  reason: string | null;
  document_id: string | null;
  resolved_pdf: string | null;
  bboxes: BBox[];
  page_supported?: boolean;
  suggested_page?: number | null;
  /** Added by the server for a sentence the model left uncited but the notes clearly support. */
  inferred?: boolean;
}

export interface CitationReport {
  citations: Citation[];
  valid_count: number;
  invalid_count: number;
  page_mismatch_count?: number;
  inferred_count?: number;
  /** The answer with inferred citations inserted (null when nothing was added). */
  annotated_answer?: string | null;
  uncited_sentences: string[];
  claim_count: number;
  coverage: number;
  grounded: boolean;
}

export interface PublicQuestion {
  question_id: number;
  bloom_level: BloomLevel;
  question_stem: string;
  options: { label: string; text: string }[];
  hint: string;
  source_pdf: string;
  source_page: number;
  section_title: string;
  document_id: string;
}

export interface GradedOption {
  label: string;
  text: string;
  is_correct: boolean;
  trap_type: TrapType;
  misconception_explanation: string;
}

export interface AnswerResult {
  quiz_id: string;
  question_id: number;
  selected_label: string;
  is_correct: boolean;
  correct_label: string;
  attempt_number: number;
  first_try: boolean;
  mastery_updated: boolean;
  mastery_before: number | null;
  mastery_after: number | null;
  options: GradedOption[];
  source_quote: string;
  source_pdf: string;
  source_page: number;
  source_bboxes: BBox[];
  document_id: string;
  section_title: string;
}

export interface MasterySection {
  parent_id: string;
  section_title: string;
  lecture_title: string;
  pdf_name: string;
  document_id: string;
  page_start: number;
  page_end: number;
  score: number;
  attempts: number;
  correct: number;
}

export interface MasterySummary {
  course_id: string;
  overall: number;
  sections: MasterySection[];
  attempted_sections: number;
  total_sections: number;
}

export interface Health {
  status: string;
  profile: string;
  llm: { ok: boolean; provider: string; model: string; detail: string };
  models: { embedding: string; reranker: string; llm: string; parser: string };
}

export interface QuizRequestBody {
  course_id: string;
  topic?: string | null;
  num_questions: number;
  bloom_levels: BloomLevel[];
  document_ids?: string[] | null;
  adaptive: boolean;
}

export interface ChatTurn { role: "user" | "assistant"; content: string }

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/health"),
  listCourses: () => request<Course[]>("/courses"),
  createCourse: (name: string, code?: string) => request<Course>("/courses", json({ name, code: code || null })),
  createSampleCourse: () => request<{ course: Course; documents: DocumentInfo[] }>("/courses/sample", { method: "POST" }),
  deleteCourse: (id: string) => request<void>(`/courses/${id}`, { method: "DELETE" }),
  listDocuments: (courseId: string) => request<DocumentInfo[]>(`/courses/${courseId}/documents`),
  uploadDocuments: (courseId: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return request<UploadResult>(`/courses/${courseId}/documents`, { method: "POST", body: form });
  },
  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  reprocessDocument: (id: string) => request<DocumentInfo>(`/documents/${id}/reprocess`, { method: "POST" }),
  documentMarkdown: (id: string) =>
    request<{ document_id: string; pdf_name: string; engine: string; markdown: string }>(`/documents/${id}/markdown`),
  documentFileUrl: (id: string) => `${BASE}/documents/${id}/file`,
  mastery: (courseId: string) => request<MasterySummary>(`/courses/${courseId}/mastery`),
  answer: (quizId: string, questionId: number, label: string) =>
    request<AnswerResult>(`/quizzes/${quizId}/questions/${questionId}/answer`, json({ label })),
  chat: (
    body: { course_id: string; question: string; history: ChatTurn[]; document_ids?: string[] | null; expansion?: Expansion },
    onEvent: (e: SSEEvent) => void,
    signal?: AbortSignal,
  ) => streamSSE(`${BASE}/chat`, body, onEvent, signal),
  quiz: (body: QuizRequestBody, onEvent: (e: SSEEvent) => void, signal?: AbortSignal) =>
    streamSSE(`${BASE}/quiz`, body, onEvent, signal),
};

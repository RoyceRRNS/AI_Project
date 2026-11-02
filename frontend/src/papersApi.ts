import { json, request, type BBox, type CitationReport } from "./api";
import { streamSSE, type SSEEvent } from "./sse";

export type PaperStatus = "queued" | "parsing" | "extracting" | "classifying" | "mapping" | "ready" | "failed";
export type QuestionType =
  | "definition" | "explain_compare" | "derivation" | "numerical" | "algorithm" | "diagram" | "case_based";

export const QUESTION_TYPES: { key: QuestionType; label: string }[] = [
  { key: "definition", label: "Definition" },
  { key: "explain_compare", label: "Explain / compare" },
  { key: "derivation", label: "Derivation" },
  { key: "numerical", label: "Numerical" },
  { key: "algorithm", label: "Algorithm" },
  { key: "diagram", label: "Diagram" },
  { key: "case_based", label: "Case-based" },
];

export interface PaperPart {
  name: string;
  instructions: string;
  answer_count: number | null;
  each_marks: number | null;
  stated_total: number | null;
}

export interface PaperInfo {
  id: string;
  course_id: string;
  pdf_name: string;
  size_bytes: number;
  title: string | null;
  exam_type: string | null;
  year: number | null;
  term: string | null;
  max_marks: number | null;
  duration: string | null;
  parts: PaperPart[];
  warnings: string[];
  status: PaperStatus;
  progress: number;
  error: string | null;
  engine: string | null;
  num_pages: number | null;
  question_count: number;
  marks_found: number;
  created_at: string;
  updated_at: string;
}

export interface PaperQuestion {
  id: string;
  number: string;
  sub: string;
  label: string;
  part: string;
  choice_group: string;
  context: string;
  text: string;
  marks: number | null;
  marks_estimated: boolean;
  page: number;
  question_type: QuestionType | null;
  bloom_level: string | null;
  section_title: string | null;
  lecture_title: string | null;
  topic_score: number | null;
}

export interface PaperDetail extends PaperInfo {
  questions: PaperQuestion[];
}

export interface UploadPapersResult {
  papers: PaperInfo[];
  errors: { filename: string; detail: string; status: number }[];
}

export interface PastQuestion {
  question_id: string;
  paper_id: string;
  paper_title: string;
  year: number | null;
  label: string;
  part: string;
  text: string;
  context: string;
  marks: number | null;
  page: number | null;
}

export interface ModuleRow {
  module: string;
  document_id: string | null;
  notes_pdf: string | null;
  mapped: boolean;
  marks: number;
  share: number;
  marks_per_paper: number;
  expected_marks: number;
  questions: number;
  papers: number;
  frequency: number;
  years: number[];
  types: Record<string, number>;
}

export interface SectionRow extends ModuleRow {
  parent_id: string | null;
  section_title: string;
  page_start: number;
  page_end: number;
  examples: PastQuestion[];
}

export interface MixRow { key: string; label?: string; count: number; marks: number; share: number }

export interface PartStructure {
  name: string;
  instructions: string;
  questions: number;
  required: number;
  marks_each: number | null;
  total: number;
  either_or: boolean;
  choice: boolean;
  pattern: string;
}

export interface PaperStructure {
  paper_id: string;
  title: string;
  exam_type: string | null;
  year: number | null;
  max_marks: number | null;
  duration: string | null;
  total_marks: number;
  parts: PartStructure[];
  signature: string;
  paper_count?: number;
}

export interface RecurringQuestion {
  text: string;
  section_title: string | null;
  parent_id: string | null;
  question_type: QuestionType | null;
  marks: number | null;
  papers: number;
  years: number[];
  instances: PastQuestion[];
}

export interface Phrasing {
  phrasing: string;
  count: number;
  question_type: QuestionType | null;
  typical_marks: number | null;
  examples: PastQuestion[];
}

export interface PracticeMix { mode: string; label: string; marks: number; share: number }

export interface PaperSummary {
  papers: number;
  questions: number;
  years: number[];
  marks: number;
  marks_per_paper: number;
  coverage: number;
  numericals: number;
  unmarked: number;
}

export interface Blueprint {
  summary: PaperSummary;
  modules: ModuleRow[];
  sections: SectionRow[];
  types: MixRow[];
  bloom: MixRow[];
  marks_distribution: { marks: number; count: number }[];
  structures: PaperStructure[];
  typical: PaperStructure | null;
  recurring: RecurringQuestion[];
  phrasings: Phrasing[];
  practice_mix: PracticeMix[];
  gaps: PastQuestion[];
  stale_questions: number;
}

export interface PlanItem {
  rank: number;
  priority: number;
  tier: "must" | "should" | "optional";
  tier_label: string;
  parent_id: string | null;
  section_title: string;
  module: string;
  notes_pdf: string | null;
  document_id: string | null;
  page_start: number;
  page_end: number;
  marks_per_paper: number;
  expected_marks: number;
  frequency: number;
  papers: number;
  years: number[];
  examples: PastQuestion[];
  mastery: number;
  mastery_attempts: number;
  focus: string;
  focus_label: string;
  practice: { mode: string; label: string; share: number }[];
  advice: string;
}

export interface PlanDay {
  day: number;
  title: string;
  sections: string[];
  focus: string[];
  expected_marks: number;
}

export interface RevisionPlan {
  summary: PaperSummary;
  items: PlanItem[];
  practice_mix: PracticeMix[];
  gaps: PastQuestion[];
  stale_questions: number;
  schedule: PlanDay[];
  days: number | null;
}

export interface MockQuestion {
  ord: number;
  label: string;
  number: string;
  sub: string;
  part: string;
  choice_group: string;
  marks: number;
  question_type: QuestionType;
  type_label: string;
  bloom_level: string;
  question: string;
  solution: string;
  final_answer: string;
  answer_check: { expression: string; value: number } | null;
  marking: { point: string; marks: number }[];
  report: CitationReport;
  section_title: string;
  lecture_title: string;
  parent_id: string;
  document_id: string;
  pdf_name: string;
  evidence: { quote: string; page: number; bboxes: BBox[] } | null;
}

export interface MockSpec {
  kind: "paper" | "practice";
  topic?: string | null;
  template_title?: string;
  exam_type?: string | null;
  duration?: string | null;
  parts?: PartStructure[];
  max_marks?: number;
}

export interface MockSummary { id: string; kind: string; title: string; created_at: string; question_count: number }

export interface MockPaper {
  id: string;
  course_id: string;
  kind: string;
  title: string;
  spec: MockSpec;
  created_at: string;
  questions: MockQuestion[];
}

export interface MockRequestBody {
  course_id: string;
  kind: "paper" | "practice";
  num_questions?: number;
  question_types?: QuestionType[] | null;
  topic?: string | null;
  template_paper_id?: string | null;
}

export const papersApi = {
  list: (courseId: string) => request<PaperInfo[]>(`/courses/${courseId}/papers`),
  upload: (courseId: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return request<UploadPapersResult>(`/courses/${courseId}/papers`, { method: "POST", body: form });
  },
  addSamples: (courseId: string) =>
    request<{ papers: PaperInfo[]; skipped: string[] }>(`/courses/${courseId}/papers/sample`, { method: "POST" }),
  remap: (courseId: string) => request<{ queued: number }>(`/courses/${courseId}/papers/remap`, { method: "POST" }),
  blueprint: (courseId: string) => request<Blueprint>(`/courses/${courseId}/papers/blueprint`),
  plan: (courseId: string, days?: number | null) =>
    request<RevisionPlan>(`/courses/${courseId}/papers/plan${days ? `?days=${days}` : ""}`),
  get: (id: string) => request<PaperDetail>(`/papers/${id}`),
  update: (id: string, fields: { title?: string; year?: number | null; exam_type?: string | null }) =>
    request<PaperInfo>(`/papers/${id}`, { ...json(fields), method: "PATCH" }),
  remove: (id: string) => request<void>(`/papers/${id}`, { method: "DELETE" }),
  reprocess: (id: string) => request<PaperInfo>(`/papers/${id}/reprocess`, { method: "POST" }),
  fileUrl: (id: string) => `/api/papers/${id}/file`,
  listMocks: (courseId: string) => request<MockSummary[]>(`/courses/${courseId}/mocks`),
  getMock: (id: string) => request<MockPaper>(`/mocks/${id}`),
  removeMock: (id: string) => request<void>(`/mocks/${id}`, { method: "DELETE" }),
  mock: (body: MockRequestBody, onEvent: (e: SSEEvent) => void, signal?: AbortSignal) =>
    streamSSE("/api/papers/mock", body, onEvent, signal),
};

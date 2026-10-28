import { vi } from "vitest";
import { api } from "../api";
import { course, doc, health, mastery } from "./fixtures";

/** Spies on every API call with sensible defaults; override per test. */
export function mockBackend(over: Partial<{ [K in keyof typeof api]: unknown }> = {}) {
  const defaults = {
    health: vi.fn().mockResolvedValue(health),
    listCourses: vi.fn().mockResolvedValue([course]),
    listDocuments: vi.fn().mockResolvedValue([doc(), doc({ id: "d2", pdf_name: "Lecture_07_RNNs.pdf" })]),
    mastery: vi.fn().mockResolvedValue(mastery),
    createCourse: vi.fn(),
    createSampleCourse: vi.fn(),
    deleteCourse: vi.fn().mockResolvedValue(undefined),
    uploadDocuments: vi.fn().mockResolvedValue({ documents: [], errors: [] }),
    deleteDocument: vi.fn().mockResolvedValue(undefined),
    reprocessDocument: vi.fn().mockResolvedValue(doc()),
    documentMarkdown: vi.fn().mockResolvedValue({ document_id: "d1", pdf_name: "L.pdf", engine: "docling", markdown: "# Title\n\n$x$" }),
    answer: vi.fn(),
    chat: vi.fn(),
    quiz: vi.fn(),
  };
  const mocks = { ...defaults, ...over } as Record<string, ReturnType<typeof vi.fn>>;
  for (const [key, fn] of Object.entries(mocks)) {
    vi.spyOn(api, key as keyof typeof api).mockImplementation(fn as never);
  }
  return mocks;
}

import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { openCourse } from "./helpers";

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8765}/api`;
let courseId: string | null = null;

/** The Papers specs get their own course: app.spec.ts deletes the shared sample course. */
export async function papersCourse(request: APIRequestContext): Promise<string> {
  if (courseId) return courseId;
  const res = await request.post(`${API}/courses/sample`);
  expect(res.ok()).toBeTruthy();
  const id = (await res.json()).course.id as string;
  const deadline = Date.now() + 600_000;
  for (;;) {
    const docs = (await (await request.get(`${API}/courses/${id}/documents`)).json()) as { status: string }[];
    if (docs.length >= 4 && docs.every((d) => d.status === "ready" || d.status === "failed")) break;
    if (Date.now() > deadline) throw new Error("documents did not finish processing");
    await new Promise((r) => setTimeout(r, 500));
  }
  courseId = id;
  return id;
}

export const addSamplePapers = (request: APIRequestContext, id: string) => request.post(`${API}/courses/${id}/papers/sample`);

export const openPapers = (page: Page, id: string) => openCourse(page, id, "papers" as never);

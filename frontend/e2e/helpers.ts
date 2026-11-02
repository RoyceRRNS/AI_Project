import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { FIXTURES } from "./global-setup";

export const fixture = (name: string) => resolve(FIXTURES, name);
export const invalid = (name: string) => resolve(FIXTURES, "invalid", name);

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8765}/api`;
let sampleCourseId: string | null = null;

async function waitReady(request: APIRequestContext, courseId: string, count: number, timeoutMs = 600_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const docs = (await (await request.get(`${API}/courses/${courseId}/documents`)).json()) as { status: string }[];
    if (docs.length >= count && docs.every((d) => d.status === "ready" || d.status === "failed")) return docs;
    if (Date.now() > deadline) throw new Error("documents did not finish processing");
    await new Promise((r) => setTimeout(r, 500));
  }
}

/** A processed sample course shared by the tests in a run (ingestion is the slow part). */
export async function sampleCourse(request: APIRequestContext): Promise<string> {
  if (sampleCourseId) return sampleCourseId;
  const res = await request.post(`${API}/courses/sample`);
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  await waitReady(request, body.course.id, 4);
  sampleCourseId = body.course.id as string;
  return sampleCourseId;
}

/** Opens the app with the given course selected and a given tab active. */
export async function openCourse(page: Page, courseId: string, tab: "library" | "chat" | "quiz" = "library") {
  await page.addInitScript(
    ([id, t]) => {
      if (!sessionStorage.getItem("e2e-init")) {
        localStorage.clear();
        localStorage.setItem("cnr.course", id);
        localStorage.setItem("cnr.tab", t);
        sessionStorage.setItem("e2e-init", "1");
      }
    },
    [courseId, tab],
  );
  await page.goto("/");
  await expect(page.getByRole("tab", { name: /Library/ })).toBeVisible();
}

export async function ask(page: Page, question: string) {
  const box = page.getByLabel("Your question");
  await expect(box).toBeEnabled();
  await box.fill(question);
  await box.press("Enter");
}

// Captures product screenshots from a running instance (UI on :5173, API on :8000) for the
// report and slide deck:  node scripts/capture-screens.mjs <courseId> <outDir>
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const [courseId, outArg = "../docs/figures"] = process.argv.slice(2);
if (!courseId) throw new Error("usage: node scripts/capture-screens.mjs <courseId> [outDir]");
const out = resolve(outArg);
mkdirSync(out, { recursive: true });
const BASE = process.env.UI_URL ?? "http://localhost:5173";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: "light" });
await ctx.addInitScript((id) => {
  if (!sessionStorage.getItem("init")) {
    localStorage.clear();
    localStorage.setItem("cnr.course", id);
    localStorage.setItem("cnr.tab", "library");
    sessionStorage.setItem("init", "1");
  }
}, courseId);
const page = await ctx.newPage();
page.setDefaultTimeout(240_000);
const shot = async (name) => {
  await page.waitForTimeout(600);
  await page.screenshot({ path: resolve(out, `${name}.png`) });
  console.log("saved", name);
};

// 1. Library
await page.goto(BASE);
await page.getByText("Ready for chat and quizzes").first().waitFor();
await page.getByText("Sample course created").waitFor({ state: "detached" }).catch(() => {});
await shot("ui_library");

// 2. Parsed-content preview (LaTeX + table)
await page.getByRole("button", { name: "Preview Lecture_04_SVMs.pdf" }).click();
const drawer = page.getByRole("dialog", { name: "Extracted content" });
await drawer.locator(".katex").first().waitFor();
await drawer.locator(".drawer-body").evaluate((el) => (el.scrollTop = el.scrollHeight * 0.38));
await shot("ui_preview");
await drawer.getByRole("button", { name: "Close preview" }).click();

// 3. Grounded chat with Qwen, sources and the PDF viewer
await page.getByRole("tab", { name: /Chat/ }).click();
const box = page.getByLabel("Your question");
await box.fill("Why does the vanishing gradient problem happen in RNNs and how do LSTMs fix it?");
await box.press("Enter");
await page.getByTestId("grounding").waitFor();
await page.getByRole("button", { name: /\d sources?/ }).first().click();
await page.getByRole("button", { name: /Open citation/ }).first().click();
await page.getByRole("complementary", { name: "PDF viewer" }).locator("canvas").first().waitFor();
await page.waitForTimeout(1500);
await shot("ui_chat_viewer");
await page.keyboard.press("Escape");

// 4. Not found
await box.fill("What is the capital of Australia?");
await box.press("Enter");
await page.getByTestId("not-found").waitFor();
await shot("ui_chat_not_found");

// 5. Quiz: generate with Qwen, answer, review explanations
await page.getByRole("tab", { name: /Quiz/ }).click();
await page.getByLabel("Quiz topic").fill("KKT conditions and support vectors");
await page.getByLabel("Number of questions").fill("3");
await shot("ui_quiz_setup");
await page.getByRole("button", { name: /Generate quiz/ }).click();
await page.getByTestId("question-card").waitFor();
await page.getByRole("button", { name: /Show hint/ }).click();
await page.getByTestId("option-B").click();
await shot("ui_quiz_question");
await page.getByRole("button", { name: "Check answer" }).click();
await page.locator(".feedback").waitFor();
await page.getByTestId("question-card").evaluate((el) => el.scrollIntoView({ block: "start" }));
await page.locator(".panel").evaluate((el) => el.scrollBy(0, -12));
await shot("ui_quiz_feedback");
// Answer the rest (the model may skip a question that fails validation, so don't assume 3).
for (;;) {
  const next = page.getByRole("button", { name: /Next question|See results/ });
  await next.waitFor();
  await page.waitForFunction(() => !document.querySelector("[data-testid=generating]"));
  if (/See results/.test(await next.innerText())) break;
  await next.click();
  await page.getByTestId("option-A").click();
  await page.getByRole("button", { name: "Check answer" }).click();
  await page.locator(".feedback").waitFor();
}
await page.getByRole("button", { name: /See results/ }).click();
await page.getByTestId("results").waitFor();
await page.getByTestId("results").evaluate((el) => el.scrollIntoView({ block: "start" }));
await page.locator(".panel").evaluate((el) => el.scrollBy(0, -12));
await shot("ui_quiz_results");

// 6. Mobile layout
const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: "light", isMobile: true, hasTouch: true });
await mobile.addInitScript((id) => { localStorage.setItem("cnr.course", id); localStorage.setItem("cnr.tab", "quiz"); }, courseId);
const m = await mobile.newPage();
await m.goto(BASE);
await m.getByTestId("mastery-row").first().waitFor();
await m.waitForTimeout(800);
await m.screenshot({ path: resolve(out, "ui_mobile.png") });
console.log("saved ui_mobile");

await browser.close();

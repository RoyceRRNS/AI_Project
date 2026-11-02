import { expect, test } from "@playwright/test";
import { ask, fixture, invalid, openCourse, sampleCourse } from "./helpers";

test.describe.configure({ mode: "serial" });

test("first visit: create a course, upload lecture PDFs and preview the parsed content", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Study smarter with your lecture notes" })).toBeVisible();
  await page.getByRole("button", { name: "Create a course" }).click();

  const dialog = page.getByRole("dialog", { name: "Create course" });
  await dialog.getByRole("button", { name: "Create course" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Give the course a name");
  await dialog.getByLabel("Course name").fill("Deep Learning");
  await dialog.getByLabel("Course code").fill("CS230");
  await dialog.getByRole("button", { name: "Create course" }).click();

  await expect(page.getByRole("button", { name: "Switch course" })).toContainText("CS230");
  await expect(page.getByText("No documents yet")).toBeVisible();
  await page.getByTestId("file-input").setInputFiles([fixture("Lecture_04_SVMs.pdf"), fixture("Lecture_02_Probability_Slides.pdf")]);

  const cards = page.getByTestId("doc-card");
  await expect(cards).toHaveCount(2);
  await expect(page.getByText("Ready for chat and quizzes")).toHaveCount(2);
  await expect(cards.filter({ hasText: "Probability Slides" }).getByText("Slides", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Library summary")).toContainText("2 ready");

  await page.getByRole("button", { name: "Preview Lecture_04_SVMs.pdf" }).click();
  const drawer = page.getByRole("dialog", { name: "Extracted content" });
  await expect(drawer.getByRole("heading", { name: "Lecture 4: Support Vector Machines" })).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "3.2 KKT Conditions" })).toBeVisible();
  await expect(drawer.locator(".katex-display").first()).toBeVisible();
  await expect(drawer.locator("table")).toContainText("Gaussian (RBF)");
  await drawer.getByRole("button", { name: "Close preview" }).click();
  await expect(drawer).toBeHidden();
});

test("invalid uploads are rejected with clear messages", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId);
  await page.getByTestId("file-input").setInputFiles([invalid("not_a_pdf.pdf")]);
  await expect(page.getByRole("alert").filter({ hasText: "is not a PDF file" })).toBeVisible();
  // Uploading the same file twice: the second copy is refused.
  await page.getByTestId("file-input").setInputFiles([fixture("Lecture_07_RNNs.pdf")]);
  await expect(page.getByText(/Uploading 1 file/)).toBeVisible();
  await page.getByTestId("file-input").setInputFiles([fixture("Lecture_07_RNNs.pdf")]);
  await expect(page.getByRole("alert").filter({ hasText: "already in this course" })).toBeVisible();
  await page.getByTestId("file-input").setInputFiles([invalid("empty.pdf")]);
  await expect(page.getByRole("alert").filter({ hasText: "empty.pdf is empty" })).toBeVisible();
});

test("grounded chat: cited answer, verified chips, sources, and the PDF opens at the cited paragraph", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId, "chat");
  await expect(page.getByRole("heading", { name: /Ask anything about CS229-lite/ })).toBeVisible();
  await ask(page, "What does complementary slackness imply for the multipliers?");

  await expect(page.getByTestId("grounding")).toContainText("verified");
  const chips = page.getByRole("button", { name: /Open citation: Lecture 04 SVMs, page \d/ });
  await expect(chips.first()).toBeVisible();
  await expect(chips.first()).toHaveClass(/cite-valid/);

  await page.getByRole("button", { name: /\d sources?/ }).click();
  await expect(page.locator(".source").first()).toContainText("Lecture 04 SVMs");

  await chips.first().click();
  const viewer = page.getByRole("complementary", { name: "PDF viewer" });
  await expect(viewer).toBeVisible();
  await expect(viewer.getByTestId("viewer-page")).toHaveText(/^\d \/ 3$/);
  await expect(viewer.locator("canvas").first()).toBeVisible();
  await expect(viewer.getByTestId("pdf-highlight").first()).toBeVisible();
  await viewer.getByRole("button", { name: "Next page" }).click();
  await expect(viewer.getByTestId("pdf-highlight")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
});

test("chat refuses to guess when the notes do not cover the question", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId, "chat");
  await ask(page, "How do chloroplasts carry out photosynthesis?");
  await expect(page.getByTestId("not-found")).toContainText("Not found in your course notes");
});

test("chat can be scoped to one document and keeps follow-up context", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId, "chat");
  await page.getByRole("button", { name: /All documents/ }).click();
  await page.getByRole("listbox", { name: "Search scope" }).getByLabel("Lecture 07 RNNs", { exact: true }).check();
  await page.keyboard.press("Escape");
  await page.getByLabel("Your question").click();
  await ask(page, "What does the forget gate do?");
  await expect(page.getByTestId("grounding")).toBeVisible();
  await page.getByRole("button", { name: /\d sources?/ }).click();
  await expect(page.locator(".source")).not.toContainText(["Lecture 04 SVMs"]);
  await expect(page.locator(".source").first()).toContainText("Lecture 07 RNNs");

  await ask(page, "And how does the forget gate stop gradients from vanishing?");
  await expect(page.getByTestId("grounding")).toHaveCount(2);
  // History survives a reload.
  await page.reload();
  await expect(page.getByText("And how does the forget gate stop gradients from vanishing?")).toBeVisible();
  await page.getByRole("button", { name: /New chat/ }).click();
  await expect(page.getByRole("heading", { name: /Ask anything/ })).toBeVisible();
});

test("quiz: generate, answer by keyboard, review explanations, mastery updates and results", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId, "quiz");
  await expect(page.getByRole("heading", { name: "Practice quiz" })).toBeVisible();
  const before = await page.locator(".overall").innerText();
  await page.getByLabel("Number of questions").fill("3");
  await page.getByRole("button", { name: /Generate quiz/ }).click();

  for (let i = 1; i <= 3; i++) {
    const card = page.getByTestId("question-card");
    await expect(card).toContainText(`Question ${i} of 3`);
    if (i === 1) {
      await card.getByRole("button", { name: /Show hint/ }).click();
      await expect(page.getByRole("note")).toBeVisible();
    }
    await page.keyboard.press(String(i));
    await expect(page.getByTestId(`option-${"ABC"[i - 1]}`)).toHaveClass(/selected/);
    await page.keyboard.press("Enter");
    const feedback = page.locator(".feedback");
    await expect(feedback).toBeVisible();
    await expect(feedback.getByTestId("mastery-delta")).toBeVisible();
    await expect(card.locator(".trap.correct")).toHaveCount(1);
    await expect(card.locator(".trap")).toHaveCount(4);
    await expect(feedback.locator(".quote")).toContainText("p.");
    await page.keyboard.press("Enter");
  }

  const results = page.getByTestId("results");
  await expect(results).toBeVisible();
  await expect(results).toContainText(/\d\/3/);
  await expect(page.locator(".overall")).not.toHaveText(before);
  await expect(page.locator(".overall")).toContainText(/[1-3]\/\d+ sections practised/);
});

test("flashcards: make a deck, flip by keyboard, grade cards and open the source page", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId, "flashcards");
  await expect(page.getByRole("heading", { name: "Flashcards" })).toBeVisible();
  await page.getByRole("button", { name: /Make flashcards/ }).click();
  await expect(page.getByText(/Made \d+ new cards/)).toBeVisible();
  const summary = page.getByLabel("Deck summary");
  await expect(summary).toContainText("formulas");
  await expect(summary).toContainText("key points");

  // Browse and filter: every formula renders as math.
  await page.getByRole("button", { name: "Formulas", exact: true }).click();
  await page.getByRole("button", { name: /^Browse \d+ cards/ }).click();
  const tiles = page.getByTestId("card-tile");
  await expect(tiles.first()).toBeVisible();
  const formulaCount = await tiles.count();
  expect(formulaCount).toBeGreaterThan(0);
  await expect(page.locator(".fc-tile-back .katex-display")).toHaveCount(formulaCount);
  await expect(page.locator(".fc-tile-back .katex-error")).toHaveCount(0);

  // Study the formulas: the answer stays hidden until the card is flipped.
  await page.getByRole("button", { name: new RegExp(`Study ${formulaCount} due`) }).click();
  await expect(page.getByText(`Card 1 of ${formulaCount}`)).toBeVisible();
  await expect(page.getByRole("button", { name: /Got it/ })).toHaveCount(0);
  await page.keyboard.press("2");
  await expect(page.getByText(`Card 1 of ${formulaCount}`)).toBeVisible();
  await page.keyboard.press("Space");
  await expect(page.getByTestId("flashcard")).toHaveClass(/flipped/);
  await expect(page.getByTestId("card-back").locator(".katex").first()).toBeVisible();

  // The source link opens the highlighted page.
  await page.locator(".fc-after").getByRole("button", { name: /· p\. \d+/ }).click();
  const viewer = page.getByRole("complementary", { name: "PDF viewer" });
  await expect(viewer.locator("canvas")).toBeVisible();
  await expect(viewer.getByTestId("pdf-highlight").first()).toBeVisible();
  await viewer.getByRole("button", { name: "Close viewer" }).click();
  await expect(viewer).toBeHidden();

  // "Again" brings the card back at the end of the session; "Got it" moves on.
  await page.keyboard.press("1");
  await expect(page.getByText(`Card 2 of ${formulaCount + 1}`)).toBeVisible();
  for (let i = 2; i <= formulaCount + 1; i++) {
    await expect(page.getByText(`Card ${i} of ${formulaCount + 1}`)).toBeVisible();
    await page.keyboard.press("Space");
    await expect(page.getByTestId("flashcard")).toHaveClass(/flipped/);
    await page.keyboard.press("2");
  }
  await expect(page.getByTestId("session-done")).toContainText(`${formulaCount} known · 1 to review again`);
  await page.getByRole("button", { name: "Back to deck" }).click();

  // Progress survives a reload: the known cards are no longer due.
  await page.reload();
  await page.getByRole("button", { name: "Formulas", exact: true }).click();
  await expect(page.getByLabel("Deck summary")).toContainText("0due now");
  await expect(page.getByTestId("caught-up")).toContainText("All caught up");
});

test("weak spots: practise a section straight from the mastery panel", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId, "quiz");
  const weakest = page.getByTestId("mastery-row").first();
  await expect(weakest).toBeVisible();
  await weakest.getByRole("button", { name: "Practise" }).click();
  await expect(page.getByTestId("question-card")).toBeVisible();
  await page.getByTestId("option-A").click();
  await page.getByRole("button", { name: "Check answer" }).click();
  await expect(page.locator(".feedback")).toBeVisible();
  // Opening the source quote shows the lecture page in the viewer.
  await page.locator(".quote .link-btn").click();
  await expect(page.getByRole("complementary", { name: "PDF viewer" }).locator("canvas").first()).toBeVisible();
});

test("documents and courses can be deleted", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId);
  await expect(page.getByTestId("doc-card").first()).toBeVisible();
  const count = await page.getByTestId("doc-card").count();
  await page.getByRole("button", { name: "Delete Handwritten_Gradient_Descent.pdf" }).click();
  await page.getByRole("dialog", { name: "Confirm delete" }).getByRole("button", { name: "Delete" }).click();
  await expect(page.getByTestId("doc-card")).toHaveCount(count - 1);
  await expect(page.getByText("Deleted Handwritten_Gradient_Descent.pdf")).toBeVisible();

  await page.getByRole("button", { name: "Switch course" }).click();
  await page.getByRole("menuitem", { name: /Delete this course/ }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText(/Deleted course/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Switch course" })).not.toContainText("CS229-lite");
});

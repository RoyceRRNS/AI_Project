import { expect, test } from "@playwright/test";
import { fixture, invalid } from "./helpers";
import { openPapers, papersCourse } from "./papers.helpers";

test.describe.configure({ mode: "serial" });

const PAPERS = ["End_Semester_2023.pdf", "End_Semester_2024.pdf", "CAT1_Fall_2025.pdf", "End_Semester_May_2022_Scan.pdf"];

test("past papers: upload every layout, then read the blueprint", async ({ page, request }) => {
  const courseId = await papersCourse(request);
  await openPapers(page, courseId);

  await expect(page.getByText("No papers yet.")).toBeVisible();
  await page.getByTestId("paper-input").setInputFiles([invalid("not_a_pdf.pdf")]);
  await expect(page.getByRole("alert").filter({ hasText: "is not a PDF file" })).toBeVisible();

  await page.getByTestId("paper-input").setInputFiles(PAPERS.map((name) => fixture(`papers/${name}`)));
  await expect(page.getByTestId("paper-card")).toHaveCount(4);
  await expect(page.getByText("Ready for the blueprint and mock papers")).toHaveCount(4);

  // Check what was read out of the table-layout paper.
  await page.getByRole("button", { name: "See questions in CAT1_Fall_2025.pdf" }).click();
  const drawer = page.getByRole("dialog", { name: "Extracted questions" });
  await expect(drawer.getByTestId("question-row")).toHaveCount(4);
  await expect(drawer.getByTestId("question-row").first()).toContainText("State Mercer's theorem");
  await drawer.getByRole("button", { name: "Close questions" }).click();

  await page.getByRole("button", { name: "Blueprint" }).click();
  await expect(page.getByText("Papers analysed").locator("..")).toContainText("4");
  await expect(page.getByTestId("typical-structure")).toContainText("5 × 2");
  await expect(page.getByTestId("typical-structure")).toContainText("either/or choices");
  await expect(page.getByLabel("Marks by lecture")).toContainText("Lecture 4");
  await expect(page.getByLabel("Marks by question type")).toContainText("Numerical / problem solving");
  await expect(page.getByTestId("recurring").filter({ hasText: "State Bayes' theorem." })).toContainText("2 papers");
  await expect(page.getByTestId("phrasing").filter({ hasText: "Derive …" })).toContainText("Derive the dual");

  // A section opens the notes it came from.
  await page.getByTestId("section-row").first().getByRole("button").click();
  const viewer = page.getByRole("complementary", { name: "PDF viewer" });
  await expect(viewer.locator("canvas").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
});

test("revision plan: priorities, what to practise, and a day-by-day schedule", async ({ page, request }) => {
  const courseId = await papersCourse(request);
  await openPapers(page, courseId);
  await page.getByRole("button", { name: "Revision plan" }).click();

  const must = page.getByRole("region", { name: "Must revise" });
  await expect(must.getByTestId("plan-item").first()).toContainText("Asked in");
  await must.getByRole("button", { name: /Past questions/ }).first().click();
  await expect(must.locator(".past-question").first()).toBeVisible();

  await page.getByLabel("Days until the exam").fill("5");
  await expect(page.getByLabel("Daily schedule")).toContainText("Timed mock paper");
  await page.getByLabel("Days until the exam").fill("99");
  await expect(page.getByRole("alert").filter({ hasText: "Enter a number from 1 to 60." })).toBeVisible();
});

test("mock paper: same pattern as the past papers, worked solutions cite the notes", async ({ page, request }) => {
  const courseId = await papersCourse(request);
  await openPapers(page, courseId);
  await page.getByRole("button", { name: "Mock paper" }).click();
  await page.getByRole("button", { name: "Generate" }).click();

  const sheet = page.getByTestId("mock-sheet");
  await expect(sheet.getByRole("heading", { name: /Mock paper in the style of/ })).toBeVisible();
  await expect(page.getByTestId("generating")).toBeHidden();
  await expect(sheet).toContainText("40 marks");
  await expect(sheet.getByRole("region", { name: "Part A" }).getByTestId("mock-question")).toHaveCount(5);
  await expect(sheet.getByRole("region", { name: "Part B" }).locator(".mock-or")).toHaveCount(3);

  const numerical = sheet.getByTestId("mock-question").filter({ hasText: "Numerical / problem solving" }).first();
  await numerical.getByRole("button", { name: /Show worked solution/ }).click();
  await expect(numerical.getByTestId("answer-check")).toContainText("Arithmetic rechecked");
  const chip = numerical.getByRole("button", { name: /Open citation/ }).first();
  await expect(chip).toHaveClass(/cite-valid/);
  await chip.click();
  const viewer = page.getByRole("complementary", { name: "PDF viewer" });
  await expect(viewer.getByTestId("pdf-highlight").first()).toBeVisible();
  await page.keyboard.press("Escape");

  // It is saved, and a section from the plan feeds a focused practice set.
  await sheet.getByRole("button", { name: "Back" }).click();
  await expect(page.getByRole("complementary", { name: "Saved papers" })).toContainText("Mock paper in the style of");
  await page.getByRole("button", { name: "Revision plan" }).click();
  await page.getByRole("button", { name: /Practise this/ }).first().click();
  await expect(page.getByLabel("Practice topic")).not.toHaveValue("");
  await page.getByRole("button", { name: "Numerical", exact: true }).click();
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.getByTestId("mock-question")).toHaveCount(3);
  await expect(page.getByTestId("mock-sheet")).toContainText("Practice set:");
});

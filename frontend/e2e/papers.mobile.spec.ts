import { expect, test } from "@playwright/test";
import { addSamplePapers, openPapers, papersCourse } from "./papers.helpers";

test("mobile: every view of the Papers tab fits the screen", async ({ page, request }) => {
  const courseId = await papersCourse(request);
  await addSamplePapers(request, courseId);
  await openPapers(page, courseId);
  await expect(page.getByRole("button", { name: /^Papers/ })).toBeVisible();
  await page.getByRole("button", { name: /^Papers/ }).click();
  await expect(page.getByText("Ready for the blueprint and mock papers")).toHaveCount(4);

  const fits = async (label: string) => {
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${label} overflows horizontally`).toBeLessThanOrEqual(1);
  };
  await fits("Papers");
  await page.getByRole("button", { name: "Blueprint" }).click();
  await expect(page.getByText("Papers analysed")).toBeVisible();
  await fits("Blueprint");
  await page.getByRole("button", { name: "Revision plan" }).click();
  await expect(page.getByRole("region", { name: "Must revise" })).toBeVisible();
  await fits("Revision plan");
  await page.getByRole("button", { name: "Mock paper" }).click();
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.getByTestId("generating")).toBeHidden();
  await page.getByRole("button", { name: "Show all solutions" }).click();
  await fits("Mock paper");
});

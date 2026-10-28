import { expect, test } from "@playwright/test";
import { openCourse, sampleCourse } from "./helpers";

test("mobile: no horizontal scrolling and the viewer opens full screen", async ({ page, request }) => {
  const courseId = await sampleCourse(request);
  await openCourse(page, courseId);
  for (const tab of ["Library", "Chat", "Quiz"]) {
    await page.getByRole("tab", { name: new RegExp(tab) }).click();
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${tab} overflows horizontally`).toBeLessThanOrEqual(1);
  }
  await page.getByRole("tab", { name: /Library/ }).click();
  await page.getByRole("button", { name: "Open Lecture_04_SVMs.pdf" }).click();
  const viewer = page.getByRole("complementary", { name: "PDF viewer" });
  await expect(viewer.locator("canvas").first()).toBeVisible();
  const box = await viewer.boundingBox();
  const vw = page.viewportSize()!.width;
  expect(box!.width).toBeGreaterThanOrEqual(vw - 2);
  await viewer.getByRole("button", { name: "Close viewer" }).click();
  await expect(viewer).toBeHidden();
});

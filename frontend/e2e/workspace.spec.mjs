import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { approvalScenario, paginationScenario, rejectScenario, reflowScenario } from "./scenarios.mjs";

test("two gates, modify, persistence, keyboard focus and saved PDF", async ({ page, baseURL }, testInfo) => {
  await approvalScenario(page, baseURL);
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "PDF", exact: true }).click();
  const download = await pending;
  expect(await download.failure()).toBeNull();
  const path = testInfo.outputPath("synthetic-report.pdf");
  await download.saveAs(path);
  const bytes = await readFile(path);
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  expect(bytes.toString("latin1")).toContain("%%EOF");
});
test("history pagination and scoped search", async ({ page, baseURL }) => {
  await paginationScenario(page, baseURL);
});
test("rejection exports audit metadata without research execution", async ({ page, baseURL }) => {
  await rejectScenario(page, baseURL);
});
for (const width of [1440, 720, 360]) {
  test(`reflow at ${width} CSS pixels (not native zoom)`, async ({ page, baseURL }) => {
    await page.setViewportSize({ width, height: 900 });
    await reflowScenario(page, baseURL);
  });
}

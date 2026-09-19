import assert from "node:assert/strict";

// Only public page/locator operations: usable in CI and the connected browser.
export async function approvalScenario(page, baseURL) {
  await page.goto(baseURL);
  await page.getByLabel("你想研究什么？", { exact: true }).fill("合成验证：证据如何支撑研究报告？");
  await page.getByRole("button", { name: "创建并查看计划", exact: true }).click();
  await page.getByRole("button", { name: "查看规划费用估算", exact: true }).click();
  const generate = page.getByRole("button", { name: "确认并生成计划", exact: true });
  await generate.waitFor({ state: "visible" });
  assert.equal(await generate.isEnabled(), false, "Gate A must require acknowledgement");
  await page.getByRole("checkbox", { name: "我已知悉本次规划估算，确认生成计划" }).check();
  await generate.click();
  await page.getByRole("button", { name: "修改计划", exact: true }).click();
  await page.getByLabel("子问题（每行一项）", { exact: true }).fill("合成验证：限定证据范围");
  await page.getByRole("button", { name: "保存为新待审版本", exact: true }).click();
  await page.getByText("版本 2", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("新版本已保存，仍待审批，尚未执行。", { exact: true }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "批准此版本并执行", exact: true }).click();
  await page.locator(".task-heading").getByText("已完成", { exact: true }).waitFor({ state: "visible" });
  const taskURL = await page.url();
  await page.reload();
  await page.locator(".task-heading").getByText("已完成", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await page.url(), taskURL, "refresh must retain task navigation");
  await page.getByRole("link", { name: "跳转到主内容", exact: true }).press("Enter");
  assert.equal(await page.url(), taskURL, "skip link must not clear selected task");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "main");
  await page.getByRole("button", { name: "研究报告", exact: true }).click();
  await page.locator(".markdown").waitFor({ state: "visible" });
  assert.ok(await page.getByRole("button", { name: "PDF", exact: true }).isEnabled());
  return { taskURL, result: "PASS" };
}

export async function paginationScenario(page, baseURL) {
  await page.goto(baseURL);
  const more = page.getByRole("button", { name: "加载更多", exact: true });
  await more.waitFor({ state: "visible" });
  assert.equal(await page.locator(".task-card").count(), 12);
  await more.click();
  await more.waitFor({ state: "hidden" });
  assert.ok(await page.locator(".task-card").count() >= 14);
  await page.getByLabel("搜索已加载任务", { exact: true }).fill("nonexistent-synthetic-question");
  await page.getByText("未找到匹配任务", { exact: true }).waitFor({ state: "visible" });
  await page.getByLabel("搜索已加载任务", { exact: true }).fill("");
  return { result: "PASS" };
}

export async function rejectScenario(page, baseURL) {
  await page.goto(baseURL);
  await page.getByLabel("你想研究什么？", { exact: true }).fill("合成验证：拒绝后只产生审计摘要，不执行研究");
  await page.getByRole("button", { name: "创建并查看计划", exact: true }).click();
  await page.getByRole("button", { name: "查看规划费用估算", exact: true }).click();
  await page.getByRole("checkbox", { name: "我已知悉本次规划估算，确认生成计划" }).check();
  await page.getByRole("button", { name: "确认并生成计划", exact: true }).click();
  await page.getByRole("button", { name: "拒绝执行", exact: true }).click();
  await page.locator(".task-heading").getByText("已拒绝", { exact: true }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "研究报告", exact: true }).click();
  await page.locator(".markdown").waitFor({ state: "visible" });
  const report = await page.locator(".markdown").innerText();
  assert.ok(report.includes("plan_rejected") && report.includes("No runtime metrics recorded."));
  assert.ok(!report.includes("search_completed") && !report.includes("synthesis_completed"));
  return { result: "PASS" };
}

export async function reflowScenario(page, baseURL) {
  await page.goto(baseURL);
  await page.getByRole("heading", { name: "开启一项研究", exact: true }).waitFor({ state: "visible" });
  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  assert.ok(dimensions.scroll <= dimensions.client, JSON.stringify(dimensions));
  return { result: "PASS", ...dimensions };
}

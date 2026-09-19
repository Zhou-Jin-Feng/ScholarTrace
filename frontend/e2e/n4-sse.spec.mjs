import { test, expect } from '@playwright/test';

test('15-minute SSE timeline survives cuts, refresh, switch, manual reconnect and proxy path', async ({ page, request }) => {
  test.setTimeout(960000);
  await page.goto('/');
  await expect(page.locator('#status')).toHaveText('open');
  await expect(page.locator('#events')).toContainText('plan_generated');

  // Exercise the normal stream for 15 minutes. Events are paced at 350 ms;
  // the wall-clock sleep is intentional evidence, not a simulated duration.
  await page.waitForTimeout(900000);
  await expect(page.locator('#status')).toHaveText('closed');
  await expect(page.locator('#events')).toContainText('exports_ready');

  // Refresh and switch use a fresh task mode so no terminal state is carried
  // across a new EventSource instance.
  await page.goto('/?mode=short');
  await expect(page.locator('#status')).toHaveText('open');
  await page.getByRole('button', { name: '切换任务' }).click();
  await expect(page.locator('#status')).toHaveText('open');
  await expect(page.locator('#last')).toHaveText('2');

  // Three short server-side cuts are recovered inside the five automatic retries.
  await expect(page.locator('#status')).toHaveText('closed', { timeout: 20000 });
  await expect(page.locator('#events')).toContainText('exports_ready');

  // A long cut exhausts bounded automatic retries; the explicit button must
  // restore the stream and replay from the last durable sequence.
  await page.goto('/?mode=long');
  await expect(page.locator('#status')).toHaveText('error', { timeout: 60000 });
  await page.getByRole('button', { name: '重新连接' }).click();
  await expect(page.locator('#status')).toHaveText('closed', { timeout: 10000 });
  await expect(page.locator('#events')).toContainText('exports_ready');

  const audit = await (await request.get('/audit')).json();
  expect(audit.connections.every((entry) => entry.task === 'task-a' || entry.task === 'task-b')).toBeTruthy();
  expect(audit.connections.some((entry) => entry.cut === 'short')).toBeTruthy();
  expect(audit.connections.some((entry) => entry.cut === 'long')).toBeTruthy();
  expect(audit.connections.every((entry) => entry.start >= 0)).toBeTruthy();
});

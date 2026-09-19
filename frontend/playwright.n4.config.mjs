import { defineConfig } from '@playwright/test';

const browser = process.env.TEST_BROWSER || 'chromium';
export default defineConfig({
  testDir: './e2e', testMatch: 'n4-sse.spec.mjs', workers: 1, retries: 0,
  timeout: 1000000,
  outputDir: `../agent/过程记录/20260918-Notes收尾/n4-${browser}`,
  reporter: [['list'], ['junit', { outputFile: `../agent/过程记录/20260918-Notes收尾/n4-sse-${browser}.xml` }]],
  use: { baseURL: 'http://127.0.0.1:18766', browserName: browser, trace: 'retain-on-failure' },
  webServer: {
    command: '..\\.venv\\Scripts\\python.exe ../tests/n4_sse_server.py --port 18766',
    url: 'http://127.0.0.1:18766/health/live', timeout: 120000, reuseExistingServer: false,
  },
});

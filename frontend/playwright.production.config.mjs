import { defineConfig } from '@playwright/test';

const browser = process.env.TEST_BROWSER || 'chromium';
export default defineConfig({
  testDir: './e2e', testMatch: 'production.spec.mjs', workers: 1, retries: 0,
  timeout: 60000,
  outputDir: `../agent/过程记录/20260918-Notes收尾/production-browser-${browser}`,
  reporter: [['list'], ['junit', { outputFile: `../agent/过程记录/20260918-Notes收尾/production-browser30-${browser}.xml` }]],
  use: { baseURL: 'http://127.0.0.1:18765', browserName: browser, trace: 'retain-on-failure' },
  webServer: {
    command: '..\\.venv\\Scripts\\python.exe ../tests/production_browser_server.py --port 18765',
    url: 'http://127.0.0.1:18765/api/v1/health/live', reuseExistingServer: false,
  },
});

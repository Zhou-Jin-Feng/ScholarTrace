import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // N4 SSE 用例需要专用服务器与专用配置（playwright.n4.config.mjs），默认 webServer 无法承载；
  // 因此默认套件排除它，N4 仍通过 `npx playwright test --config playwright.n4.config.mjs` 独立运行。
  testIgnore: ['production.spec.mjs', 'n4-sse.spec.mjs'],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  outputDir: "../artifacts/browser-results",
  reporter: [["list"], ["junit", { outputFile: "../artifacts/browser-results.xml" }]],
  use: { baseURL: "http://127.0.0.1:18764", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }, { name: "firefox", use: { browserName: "firefox" } }],
  webServer: {
    command: "uv run --project .. python ../tests/browser_server.py --port 18764",
    url: "http://127.0.0.1:18764/api/v1/health/live",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});

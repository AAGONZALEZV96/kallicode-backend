import * as path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * Configuración oficial de Playwright E2E para KalliCode (KC-QA-B5-01).
 *
 * Aplica políticas estrictas de evidencias multimedia WORM:
 * - screenshot: only-on-failure
 * - video: retain-on-failure
 * - trace: retain-on-failure
 *
 * Configura data-testid como selector canónico (contratos/selectores-portal-v1.json).
 */
const baseURL = process.env.TEST_ENV_URL || "http://localhost:3000";

export default defineConfig({
  testDir: path.resolve(__dirname, "tests"),
  outputDir: path.resolve(__dirname, "artifacts/test-results"),
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ["list"],
    ["html", { outputFolder: path.resolve(__dirname, "artifacts/html-report"), open: "never" }],
  ],
  use: {
    baseURL,
    testIdAttribute: "data-testid",
    headless: true,
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
    actionTimeout: 10000,
    navigationTimeout: 15000,
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
  ],
  webServer: process.env.NO_WEBSERVER
    ? undefined
    : {
        command: "npm --prefix kallicode-portal run dev -- --port 3000",
        url: "http://localhost:3000",
        reuseExistingServer: true,
        timeout: 60000,
      },
});

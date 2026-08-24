import { defineConfig, devices } from "@playwright/test";

const managed = !process.env.PLAYWRIGHT_BASE_URL;
const apiPort = process.env.PLAYWRIGHT_API_PORT || "8012";
const uiPort = process.env.PLAYWRIGHT_UI_PORT || (managed ? "5174" : "5173");
const e2eData = process.env.PLAYWRIGHT_E2E_DATA || "/tmp/satria-e2e-data";
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${uiPort}`;

const backendCmd = [
  `(lsof -ti :${apiPort} | xargs kill -9) 2>/dev/null || true`,
  `(lsof -ti :${uiPort} | xargs kill -9) 2>/dev/null || true`,
  `rm -rf ${e2eData} && mkdir -p ${e2eData}/staging`,
  `cd ../backend && SADT_DATA_DIR=${e2eData} SADT_DB_PATH=${e2eData}/poc.db SADT_STAGING_DIR=${e2eData}/staging SADT_LAB_DEMO_MODE=1 SADT_E2E_SIMULATION=1 .venv/bin/python run.py --host 127.0.0.1 --port ${apiPort}`,
].join(" && ");

export default defineConfig({
  testDir: "./e2e",
  timeout: 420_000,
  use: {
    baseURL: uiUrl,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: managed
    ? [
        {
          command: backendCmd,
          url: `${apiUrl}/api/v1/auth/roles`,
          reuseExistingServer: false,
          timeout: 120_000,
          env: {
            PLAYWRIGHT_API_PORT: apiPort,
            PLAYWRIGHT_E2E_DATA: e2eData,
          },
        },
        {
          command: `SATRIA_API_PORT=${apiPort} SADT_API_PORT=${apiPort} npm run dev -- --host 127.0.0.1 --port ${uiPort}`,
          url: uiUrl,
          reuseExistingServer: false,
          timeout: 120_000,
        },
      ]
    : undefined,
});

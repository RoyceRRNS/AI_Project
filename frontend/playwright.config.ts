import { defineConfig, devices } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const python = resolve(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const API_PORT = Number(process.env.E2E_API_PORT ?? 8765);
const UI_PORT = Number(process.env.E2E_UI_PORT ?? 5174);
// E2E_PROFILE=production runs the same journeys against Docling, BGE-M3, the reranker and Qwen.
const PROFILE = process.env.E2E_PROFILE ?? "test";

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: PROFILE === "test" ? 90_000 : 600_000,
  expect: { timeout: PROFILE === "test" ? 20_000 : 240_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${UI_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `"${python}" "${resolve(root, "scripts/serve.py")}" --profile ${PROFILE} --storage-dir "${resolve(root, `storage-e2e-${PROFILE}`)}" ${PROFILE === "test" ? "--fresh" : ""} --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${UI_PORT} --strictPort`,
      url: `http://127.0.0.1:${UI_PORT}`,
      env: { VITE_API_TARGET: `http://127.0.0.1:${API_PORT}`, VITE_PORT: String(UI_PORT) },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});

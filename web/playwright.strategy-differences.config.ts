import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:4174",
    launchOptions: {
      executablePath: "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    },
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run build && npx vite preview --host=127.0.0.1 --port=4174",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});

import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8061', screenshot: 'only-on-failure',
         launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } },
  webServer: {
    command: `${process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python'} ../scripts/e2e_server.py`,
    url: 'http://127.0.0.1:8061/api/health',
    timeout: 30000,
    reuseExistingServer: false
  }
});

import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60000,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8061', screenshot: 'only-on-failure',
         // precise-memory-info: la prueba de permanencia necesita memoria sin redondear.
         launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
                          args: ['--enable-precise-memory-info'] } },
  webServer: {
    command: `${process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python'} ../scripts/e2e_server.py`,
    url: 'http://127.0.0.1:8061/api/health',
    timeout: 120000,
    reuseExistingServer: false
  }
});

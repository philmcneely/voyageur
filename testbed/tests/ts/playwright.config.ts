import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  timeout: 60000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { headless: true },
});

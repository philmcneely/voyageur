import { test, expect } from '@playwright/test';

let BASE = process.env.QA_BASE_URL || 'http://127.0.0.1:8765/drift-base/';
if (!BASE.endsWith('/')) BASE += '/';

test.use({ actionTimeout: Number(process.env.QA_ACTION_TIMEOUT_MS || 5000) });

test('profile settings', async ({ page }) => {
  await page.goto(BASE + 'index.html');
  await page.locator('#display-name').fill('Jane Doe');
  await page.getByRole('checkbox', { name: 'Email me the newsletter' }).check();
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('summary')).toHaveText('saved: profile newsletter=yes');
});

test('save by test id', async ({ page }) => {
  await page.goto(BASE + 'index.html');
  await page.getByTestId('save-settings').click();
  await expect(page.getByTestId('summary')).toHaveText('saved: profile newsletter=no');
});

test('delete account', async ({ page }) => {
  await page.goto(BASE + 'index.html');
  await page.getByRole('button', { name: 'Delete account' }).click();
  await expect(page.getByTestId('summary')).toHaveText('deleted: account');
});

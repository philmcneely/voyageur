import { test, expect } from '@playwright/test';

let BASE = process.env.QA_BASE_URL || 'http://127.0.0.1:8765/orders-base/';
if (!BASE.endsWith('/')) BASE += '/';

test.use({ actionTimeout: 5000 });

test('orders grid and toolbar', async ({ page }) => {
  await page.goto(BASE + 'index.html');
  await page.getByTestId('orders-table').locator('tbody tr').nth(3).locator('td').nth(2).locator('button').click();
  await expect(page.getByTestId('summary')).toContainText('status.1043: paid');
  await page.locator('//table//tr[3]/td[2]').click();
  await expect(page.getByTestId('summary')).toContainText('selected: 1042');
  await page.locator('#app > div > div:nth-child(2) > div.grid > div:nth-child(3) > span.cell').click();
  await expect(page.getByTestId('summary')).toContainText('selected: 1041');
  await page.getByLabel('Actions for order 1043').click();
  await page.getByRole('menuitem', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Archive' }).click();
  await expect(page.getByTestId('summary')).toContainText('archived: 1043');
  await page.getByRole('row').filter({ hasText: 'Echo GmbH' }).getByRole('checkbox').check();
  await expect(page.getByTestId('summary')).toContainText('checked: 1044');
  await page.locator('div.toolbar div.filters > button:nth-of-type(2)').click();
  await expect(page.getByTestId('summary')).toContainText('filter: paid');
  await expect(page.getByTestId('summary')).toContainText('visible: 1042,1043,1044');
  await page.locator("[data-qa='order-row-1042'] [data-qa='status']").click();
  await expect(page.getByTestId('summary')).toContainText('status.1042: shipped');
  await page.locator('[data-cy=export-csv]').click();
});

test('side panel and dialog', async ({ page }) => {
  await page.goto(BASE + 'index.html');
  await page.locator('#tab-history').click();
  await page.locator('[role=tabpanel][aria-labelledby=tab-history] li').first().click();
  await expect(page.getByTestId('summary')).toContainText('event: e301');
  await page.getByRole('tab', { name: 'Notes' }).click();
  await page.locator('#panel-notes > textarea').fill('Call before delivery');
  await page.getByRole('button', { name: 'Save note' }).click();
  await expect(page.getByTestId('summary')).toContainText('note: Call before delivery');
  await page.locator('button.sc-fzXfKy.jQdBvX').click();
  await expect(page.getByTestId('summary')).toContainText('dialog: open');
  await page.locator('[role=dialog] select[name=reason]').selectOption('duplicate');
  await expect(page.getByTestId('summary')).toContainText('reason: duplicate');
  await page.locator("[aria-label='Close dialog']").click();
  await expect(page.getByTestId('summary')).toContainText('dialog: closed');
  await page.locator('button.sc-fzXfKy.jQdBvX').click();
  await page.getByTestId('cancel-dialog').getByRole('button', { name: 'Confirm cancel' }).click();
  await expect(page.getByTestId('summary')).toContainText('cancelled: 1040');
  await expect(page.getByTestId('summary')).toContainText('dialog: closed');
});

test('payment frame and shadow', async ({ page }) => {
  await page.goto(BASE + 'index.html');
  await page.locator('input[name=zip]').fill('73301');
  await page.frameLocator('#payment-frame').locator('input[name=card]').fill('4242 4242 4242 4242');
  await page.frameLocator('#payment-frame').getByLabel('Expiry').fill('12/30');
  await page.frameLocator('#payment-frame').getByRole('button', { name: 'Pay now' }).click();
  await expect(page.getByTestId('summary')).toContainText('payment: ok');
  await expect(page.getByTestId('summary')).toContainText('card_last4: 4242');
  await expect(page.getByTestId('summary')).toContainText('exp: 12/30');
  await expect(page.getByTestId('summary')).toContainText('zip: 73301');
  await page.locator('order-flag input[name=reason]').fill('Possible fraud');
  await page.locator('order-flag button.toggle').click();
  await expect(page.getByTestId('summary')).toContainText('flag: on');
  await expect(page.getByTestId('summary')).toContainText('flag_reason: Possible fraud');
});

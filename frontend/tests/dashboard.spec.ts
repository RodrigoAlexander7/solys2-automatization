import { test, expect } from '@playwright/test';

test('shows committed simulated data and survives browser reload', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Monitor de adquisición' })).toBeVisible();
  await expect(page.getByText('SIMULADO', { exact: true })).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.locator('svg')).toHaveCount(6);
  const runs = await (await request.get('/api/runs')).json();
  const id = runs.items[0].id;
  const before = await (await request.get(`/api/runs/${id}/samples`)).json();
  await page.reload();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect.poll(async () => {
    const after = await (await request.get(`/api/runs/${id}/samples?after_id=${before.next_cursor}`)).json();
    return after.items.length;
  }).toBeGreaterThan(0);
  await page.screenshot({ path: '../test-results/dashboard-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Monitor de adquisición' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '../test-results/dashboard-mobile.png', fullPage: true });
});

test('presents API errors with a trace reference', async ({ page }) => {
  await page.route('**/api/runs', route => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ ok: false, error: { code: 'database_unavailable', message: 'Esperando adquisición' }, trace_id: 'test-trace' }) }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Esperando adquisición');
  await expect(page.getByRole('alert')).toContainText('test-trace');
});

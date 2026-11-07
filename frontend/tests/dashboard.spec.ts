import { test, expect } from '@playwright/test';

// Un solo servidor desechable (scripts/e2e_server.py): 24 h simuladas + adquisición simulada en vivo.
// Los tests se ejecutan en orden y comparten la base del observatorio.
test.describe.configure({ mode: 'serial' });

const ORIGIN = 'http://127.0.0.1:8061';

test('monitor muestra lecturas simuladas, gráficos interactivos y ventanas largas', async ({ page }) => {
  // Arranque en frío: la adquisición simulada empieza junto con la API.
  const firstLive = page.waitForResponse(r => r.url().includes('/api/live') && r.ok(), { timeout: 30000 });
  await page.goto('/');
  await firstLive;
  await expect(page.getByRole('heading', { name: 'Monitor de adquisición' })).toBeVisible();
  await expect(page.getByText('SIMULADO', { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.locator('.value-card h2', { hasText: 'Onda larga (Ld↓)' })).toBeVisible();
  await expect.poll(() => page.locator('.uplot canvas').count(), { timeout: 15000 }).toBeGreaterThan(3);
  // R28: solo el último minuto; sin ventanas largas en el monitor.
  await expect(page.getByText(/Último minuto · muestras de 1 s/)).toBeVisible();
  await expect(page.getByRole('button', { name: '24 h' })).toHaveCount(0);
  const end = new Date().toISOString(), start = new Date(Date.now() - 60_000).toISOString();
  const series = await (await page.request.get(`/api/series?start=${start}&end=${end}&resolution=1s`)).json();
  const perAddress = new Map<number, number>();
  for (const p of series.items) perAddress.set(p.address, (perAddress.get(p.address) ?? 0) + 1);
  for (const count of perAddress.values()) expect(count).toBeLessThanOrEqual(61);
  await page.screenshot({ path: '../test-results/monitor-desktop.png', fullPage: true });
});

// R31: permanencia. Opcional: SOAK_MINUTES=60 bun run --cwd frontend test:e2e -g permanencia
// Requiere --enable-precise-memory-info (playwright.config.ts): sin esa opción Chrome redondea la memoria.
test('permanencia del monitor sin crecimiento sostenido de memoria', async ({ page }) => {
  const minutes = Number(process.env.SOAK_MINUTES ?? 0);
  test.skip(!minutes, 'SOAK_MINUTES para medir la memoria del monitor durante un tiempo largo');
  test.setTimeout((minutes + 2) * 60_000);
  await page.goto('/');
  await expect.poll(() => page.locator('.uplot canvas').count(), { timeout: 30000 }).toBeGreaterThan(3);
  const heap = () => page.evaluate(() => (performance as unknown as { memory: { usedJSHeapSize: number } }).memory.usedJSHeapSize / 1e6);
  const samples: number[] = [];
  for (let i = 0; i <= minutes * 2; i++) {
    samples.push(await heap());
    if (i < minutes * 2) await page.waitForTimeout(30_000);
  }
  const settled = Math.min(...samples.slice(0, 4));
  const tail = samples.slice(-4).reduce((a, b) => a + b, 0) / Math.min(4, samples.length);
  console.log(`Heap JS (MB) cada 30 s: ${samples.map(v => v.toFixed(1)).join(' ')}`);
  expect(new Set(samples.map(v => v.toFixed(2))).size).toBeGreaterThan(1); // valores reales, no redondeados
  expect(tail - settled).toBeLessThan(15);
});

test('instrumentos: SOLYS2 y bola de sombra se configuran con historial', async ({ page }) => {
  await page.goto('/instrumentos/');
  await expect(page.getByRole('heading', { name: 'Instrumentos', level: 1 })).toBeVisible();
  await expect(page.locator('tbody tr').first()).toContainText('#2');
  await page.getByRole('switch', { name: 'SOLYS2 en uso' }).click();
  await expect(page.getByRole('switch', { name: 'SOLYS2 en uso' })).toHaveAttribute('aria-checked', 'true');
  const row4 = page.locator('form tr', { hasText: '#4' });
  await row4.getByRole('checkbox').check();
  await expect(row4).toContainText('Difusa (DHI)');
  await page.getByLabel('Sitio configurado').check();
  await page.getByLabel('Latitud (°, + norte)').fill('39.742476');
  await page.getByLabel('Longitud (°, + este)').fill('-105.1786');
  await page.getByLabel('Altura (m)').fill('1830');
  await page.getByLabel('Autor').fill('Playwright');
  await page.getByRole('button', { name: 'Guardar nueva versión' }).click();
  await expect(page.getByRole('status')).toContainText('Versión guardada');
  await expect(page.getByText('v1').first()).toBeVisible();
  await page.goto('/');
  await expect(page.locator('.value-card h2', { hasText: 'Difusa (DHI)' })).toBeVisible({ timeout: 10000 });
  await expect(page.locator('aside')).toContainText('SOLYS2 · en uso');
});

test('bitácora: nota visible en la línea de tiempo y anulable sin borrarse', async ({ page }) => {
  await page.goto('/bitacora/');
  await page.getByLabel('Autor').fill('Playwright');
  await page.getByLabel('Categoría').selectOption('limpieza');
  await page.getByLabel('Nota').fill('Limpieza de domos de prueba');
  await page.getByRole('button', { name: 'Agregar a la bitácora' }).click();
  const item = page.locator('.timeline li', { hasText: 'Limpieza de domos de prueba' });
  await expect(item).toBeVisible();
  await expect(page.locator('.timeline li', { hasText: 'Configuración v1' })).toBeVisible();
  page.once('dialog', dialog => dialog.accept('nota de prueba'));
  await item.getByRole('button', { name: 'Anular' }).click();
  await expect(item).toHaveClass(/voided/);
  await expect(item).toContainText('Anulada por Playwright');
});

test('histórico: carpet plot, estimación y descargas con QC', async ({ page, request }) => {
  const minutes = page.waitForResponse(r => r.url().includes('/api/minutes') && r.ok(), { timeout: 45000 });
  await page.goto('/historico/');
  await minutes;
  await expect(page.getByText(/≈ .* filas/)).toBeVisible({ timeout: 15000 });
  await expect(page.locator('.carpet canvas')).toBeVisible({ timeout: 15000 });
  const series = await (await request.get(`/api/series?start=${new Date(Date.now() - 86_400_000).toISOString()}&max_points=600`)).json();
  expect(series.decimated).toBe(true);
  const end = new Date().toISOString(), start = new Date(Date.now() - 3600_000).toISOString();
  const csv = await request.get(`/api/export?start=${start}&end=${end}&resolution=1s&format=csv`);
  expect(csv.ok()).toBe(true);
  const header = (await csv.text()).split('\n')[0];
  for (const column of ['role', 'qc_ppl', 'qc_shading', 'solar_zenith_deg', 'config_version']) expect(header).toContain(column);
  const figure = await request.get(`/api/figure?start=${start}&end=${end}`);
  expect(figure.headers()['content-type']).toBe('image/png');
});

test('documentación para físicos disponible sin internet', async ({ page }) => {
  await page.goto('/documentacion/');
  await expect(page.locator('nav[aria-label="Índice de documentación"] a')).toHaveCount(9);
  await page.locator('nav[aria-label="Índice de documentación"]').getByRole('link', { name: 'Calidad de datos' }).click();
  await expect(page.locator('.prose h1')).toHaveText('Calidad de datos');
  await expect(page.locator('.prose')).toContainText('Long y E. G. Dutton');
});

test('escrituras desde otro origen se rechazan', async ({ request }) => {
  const foreign = await request.post('/api/logbook', { data: { author: 'x', text: 'x' }, headers: { Origin: 'http://evil.example' } });
  expect(foreign.status()).toBe(403);
  const own = await request.post('/api/logbook', { data: { author: 'x', text: 'propia' }, headers: { Origin: ORIGIN } });
  expect(own.status()).toBe(200);
});

test('errores de la API se muestran con referencia de traza', async ({ page }) => {
  await page.route('**/api/live', route => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ ok: false, error: { code: 'database_unavailable', message: 'Esperando adquisición' }, trace_id: 'test-trace' }) }));
  await page.goto('/');
  await expect(page.getByRole('alert').first()).toContainText('Esperando adquisición');
  await expect(page.getByRole('alert').first()).toContainText('test-trace');
});

test('móvil sin desborde horizontal', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ['/', '/historico/', '/instrumentos/', '/bitacora/', '/documentacion/']) {
    await page.goto(path);
    await page.waitForTimeout(800);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), path).toBe(true);
  }
  await page.goto('/');
  await page.screenshot({ path: '../test-results/monitor-mobile.png', fullPage: true });
});

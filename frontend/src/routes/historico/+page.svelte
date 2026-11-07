<script lang="ts">
  import { onMount } from 'svelte';
  import TimeChart from '$lib/TimeChart.svelte';
  import Carpet from '$lib/Carpet.svelte';
  import { live } from '$lib/live.svelte';
  import { get, query, type ApiError, type LogbookEntry, type MinuteRow, type Point } from '$lib/api';
  import { align, colorFor, format, fromInputValue, fromMinute, fromPoint, ROLE_NAMES, toInputValue, type Group, type Row } from '$lib/data';

  const HOUR = 3_600_000, DAY = 24 * HOUR;
  const PRESETS = [
    { key: 'hour', label: 'Última hora' }, { key: 'today', label: 'Hoy' }, { key: 'yesterday', label: 'Ayer' },
    { key: '24h', label: 'Últimas 24 h' }, { key: '7d', label: 'Últimos 7 días' }, { key: '30d', label: 'Últimos 30 días' }
  ];
  let startText = $state(''), endText = $state('');
  let selected = $state<number[]>([]);
  let resolution = $state<'1s' | '1min'>('1min');
  let invalid = $state<'include' | 'exclude'>('include');
  let rows = $state<Row[]>([]);
  let carpetRows = $state<{ minute_ms: number; mean_wm2: number | null }[]>([]);
  let carpetAddress = $state<number | null>(null);
  let notes = $state<LogbookEntry[]>([]);
  let viewResolution = $state('');
  let estimate = $state<{ rows: number; approx_csv_bytes: number; max_span_days: number } | null>(null);
  let error = $state<ApiError | null>(null), loading = $state(false), truncated = $state(false);

  let config = $derived(live.data?.config);
  let known = $derived([...new Set([...(config?.instruments.map(i => i.address) ?? []), ...(live.data?.latest.map(s => s.address) ?? [])])].sort((a, b) => a - b));
  let startMs = $derived(fromInputValue(startText));
  let endMs = $derived(fromInputValue(endText));
  let span = $derived(startMs !== null && endMs !== null ? endMs - startMs : 0);
  let rangeError = $derived(startMs === null || endMs === null ? 'Fechas incompletas' : span <= 0 ? 'El inicio debe ser anterior al fin' :
    resolution === '1s' && span > 31 * DAY ? 'Con 1 s el máximo es 31 días' : span > 366 * DAY ? 'Máximo 366 días' : '');
  let params = $derived(startMs !== null && endMs !== null ? {
    start: new Date(startMs).toISOString(), end: new Date(endMs).toISOString(),
    addresses: selected.length && selected.length < known.length ? selected.join(',') : null
  } : null);
  let markers = $derived(notes.filter(n => !n.voided).map(n => ({ t_ms: n.t_ms, label: n.category_label })));
  // R28: el servidor reduce cada serie a ~600 puntos (mínimo y máximo por tramo); con
  // reducción, el paso de dibujo es medio tramo para que mínimo y máximo no se pisen.
  const MAX_POINTS = 600;
  let viewStep = $state(60_000);
  let step = $derived(viewStep);
  const SW = new Set(['GHI', 'DHI', 'SHADE_OFF']);

  function groups(filter: (r: Row) => boolean, value: (r: Row) => number | null | undefined): Group[] {
    const seen = new Map<string, Row>();
    for (const r of rows) if (filter(r)) seen.set(`${r.address}:${r.role}`, r);
    return [...seen.values()].sort((a, b) => a.address - b.address).map(r => ({
      label: `${r.label} · ${ROLE_NAMES[r.role]}`, color: colorFor(r.address, known), dashed: r.role === 'SHADE_OFF',
      filter: x => x.address === r.address && x.role === r.role, value
    }));
  }
  let shortwave = $derived(align(rows, [...groups(r => SW.has(r.role), r => r.value),
    { label: 'QC con falla', color: '#c0392b', pointsOnly: true, filter: r => SW.has(r.role) && (r.qc === 'suspect' || r.qc === 'bad'), value: r => r.value }], step));
  let longwave = $derived(align(rows, groups(r => r.role === 'LWD', r => r.value), step));
  let dni = $derived(align(rows, [{ label: 'DNI estimada', color: '#7966a8', filter: r => r.role === 'DHI', value: r => r.dni }], step));

  function preset(key: string) {
    const now = new Date();
    let start: number, end = now.getTime();
    const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
    if (key === 'hour') start = end - HOUR;
    else if (key === 'today') start = midnight.getTime();
    else if (key === 'yesterday') { end = midnight.getTime(); start = end - DAY; }
    else if (key === '24h') start = end - DAY;
    else if (key === '7d') start = end - 7 * DAY;
    else start = end - 30 * DAY;
    startText = toInputValue(start); endText = toInputValue(end);
    resolution = end - start <= 6 * HOUR ? '1s' : '1min';
  }

  async function view() {
    if (!params || rangeError) return;
    loading = true; error = null;
    const viewSpan = span > 31 * DAY ? null : span;
    const tasks: Promise<void>[] = [];
    if (viewSpan) tasks.push((async () => {
      const result = await get<{ items: (Point | MinuteRow)[]; resolution: string; truncated: boolean; bucket_ms: number; decimated: boolean }>(
        `/api/series${query({ ...params, max_points: MAX_POINTS })}`);
      if (!result.ok) { error = result.error; return; }
      viewResolution = result.value.resolution;
      truncated = result.value.truncated;
      viewStep = result.value.decimated ? result.value.bucket_ms / 2 : (result.value.resolution === '1s' ? 1000 : 60_000);
      rows = result.value.resolution === '1s' ? (result.value.items as Point[]).map(fromPoint) : (result.value.items as MinuteRow[]).map(fromMinute);
    })()); else rows = [];
    tasks.push((async () => {
      const result = await get<{ items: LogbookEntry[] }>(`/api/logbook${query({ start: params!.start, end: params!.end, include_voided: false })}`);
      if (result.ok) notes = result.value.items;
    })());
    applied = { start: params.start, end: params.end, span };
    tasks.push(loadEstimate());
    await Promise.all(tasks);
    loading = false;
  }

  // Carpet plot: intervalo aplicado con «Ver» × instrumento elegido (por defecto, el primero de global).
  let applied = $state<{ start: string; end: string; span: number } | null>(null);
  let carpetTarget = $derived(carpetAddress ?? known.find(a => live.data?.latest.find(s => s.address === a)?.role === 'GHI') ?? known[0] ?? null);
  let carpetKey = '';

  async function loadCarpet(address: number, range: { start: string; end: string }, key: string) {
    const result = await get<{ items: { minute_ms: number; address: number; mean_wm2: number | null }[] }>(
      `/api/minutes${query({ start: range.start, end: range.end, addresses: String(address) })}`);
    if (key === carpetKey && result.ok) carpetRows = result.value.items;
  }

  $effect(() => {
    if (!applied || carpetTarget === null || applied.span > 92 * DAY) return;
    const key = `${carpetTarget}|${applied.start}|${applied.end}`;
    if (key === carpetKey) return;
    carpetKey = key;
    carpetRows = [];
    void loadCarpet(carpetTarget, applied, key);
  });

  async function loadEstimate() {
    if (!params || rangeError) { estimate = null; return; }
    const result = await get<typeof estimate>(`/api/export/estimate${query({ ...params, resolution })}`);
    estimate = result.ok ? result.value : null;
  }

  function download(kind: 'csv' | 'zip' | 'png') {
    if (!params || rangeError) return;
    const url = kind === 'png'
      ? `/api/figure${query({ ...params, resolution: span <= 6 * HOUR ? 'auto' : '1min' })}`
      : `/api/export${query({ ...params, resolution, invalid, format: kind })}`;
    const link = document.createElement('a');
    link.href = url;
    if (kind !== 'png') link.download = '';
    else link.target = '_blank';
    link.click();
  }

  function toggle(address: number) {
    selected = selected.includes(address) ? selected.filter(a => a !== address) : [...selected, address].sort((a, b) => a - b);
  }

  const bytes = (n: number) => n > 1e9 ? `${format(n / 1e9, 1)} GB` : n > 1e6 ? `${format(n / 1e6, 1)} MB` : `${format(n / 1e3, 0)} kB`;

  $effect(() => { void resolution; void params; void loadEstimate(); });

  onMount(() => { preset('24h'); setTimeout(() => void view(), 0); });
</script>

<svelte:head><title>Histórico · Radiometría</title></svelte:head>

<section class="page-head">
  <div>
    <div class="eyebrow">CONSULTA POR FECHA</div>
    <h1>Histórico y descargas</h1>
    <p>Elija un intervalo para ver las series y descargar datos o figuras. Las figuras se generan al pedirlas y no se guardan. Los datos se leen de la base activa y del archivo Parquet sin duplicar filas.</p>
  </div>
</section>

<section class="card filters">
  <div class="presets">{#each PRESETS as p}<button type="button" class="btn" onclick={() => { preset(p.key); void view(); }}>{p.label}</button>{/each}</div>
  <div class="grid">
    <label class="field">Desde (hora local)<input type="datetime-local" bind:value={startText} /></label>
    <label class="field">Hasta (hora local)<input type="datetime-local" bind:value={endText} /></label>
    <label class="field">Resolución de descarga
      <select bind:value={resolution}><option value="1s">Muestras de 1 s (hasta 31 días)</option><option value="1min">Promedios de 1 min (hasta 366 días)</option></select>
    </label>
    <label class="field">Lecturas inválidas
      <select bind:value={invalid}><option value="include">Incluir (marcadas valid=0)</option><option value="exclude">Excluir</option></select>
    </label>
  </div>
  <fieldset><legend>Instrumentos (ninguno marcado = todos)</legend>
    {#each known as address}<label class="check"><input type="checkbox" checked={selected.includes(address)} onchange={() => toggle(address)} /> #{address} {config?.instruments.find(i => i.address === address)?.label ?? ''}</label>{/each}
  </fieldset>
  {#if rangeError}<div class="notice">{rangeError}</div>{/if}
  <div class="actions">
    <button type="button" class="btn primary" onclick={() => void view()} disabled={!!rangeError || loading}>{loading ? 'Cargando…' : 'Ver intervalo'}</button>
    <button type="button" class="btn" onclick={() => download('csv')} disabled={!!rangeError}>Descargar CSV</button>
    <button type="button" class="btn" onclick={() => download('zip')} disabled={!!rangeError}>Descargar ZIP con metadatos</button>
    <button type="button" class="btn" onclick={() => download('png')} disabled={!!rangeError || span > 31 * DAY}>Figura PNG</button>
    {#if estimate}<span class="estimate">≈ {estimate.rows.toLocaleString('es')} filas · CSV ≈ {bytes(estimate.approx_csv_bytes)}</span>{/if}
  </div>
</section>

{#if error}<div class="notice error" role="alert"><strong>{error.code}</strong> · {error.message}{#if error.trace_id}<small>Traza: {error.trace_id}</small>{/if}</div>{/if}
{#if truncated}<div class="notice">La vista se recortó para no saturar el navegador; la descarga incluye todas las filas.</div>{/if}

{#if span > 31 * DAY}
  <div class="notice">Intervalo mayor a 31 días: se muestra solo el carpet plot; las descargas de minutos siguen disponibles.</div>
{:else}
  <div class="section-heading"><h2>Series del intervalo</h2><span>{viewResolution === '1s' ? 'Muestras de 1 s' : 'Promedios de 1 min'}{viewStep > (viewResolution === '1s' ? 1000 : 60_000) ? ` · mínimo y máximo cada ${format(viewStep * 2 / 60_000, 0)} min para dibujar (la descarga trae todo)` : ''} · hora local · arrastre para ampliar</span></div>
  <section class="charts">
    <TimeChart title="Onda corta" unit="W/m²" data={shortwave} {markers} syncKey="historico" filename="historico-onda-corta" subtitle="Sin corrección adicional. Cruces: QC con falla." />
    <TimeChart title="Onda larga descendente" unit="W/m²" data={longwave} {markers} syncKey="historico" filename="historico-onda-larga" />
    {#if rows.some(r => r.role === 'DHI')}<TimeChart title="DNI estimada (derivada)" unit="W/m²" data={dni} {markers} syncKey="historico" filename="historico-dni" subtitle="(GHI − DHI)/cos z con cenit < 80°. No es una medición." />{/if}
  </section>
{/if}

<div class="section-heading"><h2>Carpet plot</h2>
  <label class="inline">Instrumento
    <select value={carpetTarget} onchange={e => { carpetAddress = Number(e.currentTarget.value); }}>
      {#each known as address}<option value={address}>#{address} {config?.instruments.find(i => i.address === address)?.label ?? ''}</option>{/each}
    </select>
  </label>
</div>
{#if (applied?.span ?? 0) > 92 * DAY}<div class="notice">El carpet plot admite hasta 92 días.</div>{:else if applied}
  <Carpet rows={carpetRows} startMs={Date.parse(applied.start)} endMs={Date.parse(applied.end)}
          title={`Promedios de 1 min · ${carpetTarget === null ? '' : `#${carpetTarget} ${config?.instruments.find(i => i.address === carpetTarget)?.label ?? ''}`}`} />
{/if}

<style>
  .filters { display: flex; flex-direction: column; gap: 14px; }
  .presets { display: flex; gap: 6px; flex-wrap: wrap; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  fieldset { border: 1px solid #e1e8e6; border-radius: 6px; padding: 8px 12px; display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; }
  legend { font-size: 11px; color: #5d7477; padding: 0 4px; }
  .check { display: flex; gap: 6px; align-items: center; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .estimate { font-size: 11px; color: #6f8589; }
  .charts { display: grid; grid-template-columns: 1fr; gap: 14px; }
  .inline { font-size: 11px; color: #5d7477; display: flex; gap: 8px; align-items: center; }
  .inline select { border: 1px solid #d3dedb; border-radius: 6px; padding: 4px 8px; }
</style>

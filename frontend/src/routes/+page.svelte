<script lang="ts">
  import { onMount } from 'svelte';
  import TimeChart from '$lib/TimeChart.svelte';
  import { live } from '$lib/live.svelte';
  import { get, query, type ApiError, type LogbookEntry, type Point, type Sample } from '$lib/api';
  import { align, colorFor, differenceRows, format, fromPoint, localDate, mergePoints, pairsByRole,
           QC_TEXT, ROLE_NAMES, type Group, type Row } from '$lib/data';

  // R28: el monitor muestra solo el último minuto (≤ 60 puntos por serie a 1 s) para no
  // saturar la mini-PC. Los intervalos largos se consultan en el histórico.
  const MONITOR_WINDOW_MS = 60_000;
  const SW = new Set(['GHI', 'DHI', 'SHADE_OFF']);
  const TEST_NAMES: Record<string, string> = { ppl: 'físicamente posible', erl: 'extremadamente raro', ratio: 'cociente DHI/GHI', shading: 'sombreado', comparison: 'comparación' };

  let points = $state<Point[]>([]);
  let notes = $state<LogbookEntry[]>([]);
  let loading = $state(false), error = $state<ApiError | null>(null), connected = $state(false);
  let tick = $state(Date.now());
  let source: EventSource | null = null, generation = 0, streamKey = '';

  let data = $derived(live.data);
  let run = $derived(data?.run ?? null);
  let latest = $derived(data?.latest ?? []);
  let interval = $derived(run?.metadata.config.interval_s ?? 1);
  let rows: Row[] = $derived(points.map(fromPoint));
  let addresses = $derived([...new Set([...rows.map(r => r.address), ...latest.map(s => s.address)])].sort((a, b) => a - b));
  let step = $derived(Math.max(1000, interval * 1000));
  let markers = $derived(notes.filter(n => !n.voided).map(n => ({ t_ms: n.t_ms, label: n.category_label })));
  let running = $derived(run?.state === 'running' && !data?.acquisition_stale);

  function roleGroups(filter: (r: Row) => boolean, value: (r: Row) => number | null | undefined, suffix = true): Group[] {
    const seen = new Map<string, Row>();
    for (const r of rows) if (filter(r)) seen.set(`${r.address}:${r.role}`, r);
    return [...seen.values()].sort((a, b) => a.address - b.address).map(r => ({
      label: suffix ? `${r.label} · ${ROLE_NAMES[r.role]}` : r.label, color: colorFor(r.address, addresses),
      dashed: r.role === 'SHADE_OFF', filter: x => x.address === r.address && x.role === r.role && filter(x), value
    }));
  }
  const failed = (r: Row) => r.qc === 'suspect' || r.qc === 'bad';

  let shortwave = $derived(align(rows, [
    ...roleGroups(r => SW.has(r.role), r => r.value),
    { label: 'QC con falla', color: '#c0392b', pointsOnly: true, filter: r => SW.has(r.role) && failed(r), value: r => r.value }
  ], step));
  let longwave = $derived(align(rows, [
    ...roleGroups(r => r.role === 'LWD', r => r.value),
    ...roleGroups(r => r.role === 'LWD', r => r.calc, false).map(g => ({ ...g, label: `${g.label} · neta + σT⁴`, color: '#8aa2b7', dashed: true })),
    { label: 'QC con falla', color: '#c0392b', pointsOnly: true, filter: r => r.role === 'LWD' && failed(r), value: r => r.value }
  ], step));
  let pairs = $derived(pairsByRole(rows));
  let comparison = $derived.by(() => {
    const combined: Row[] = [];
    pairs.forEach((p, i) => {
      for (const d of differenceRows(rows.filter(r => r.role === p.role), p.a, p.b)) combined.push({ ...d, address: -1 - i });
    });
    return align(combined, pairs.map((p, i) => ({
      label: `${ROLE_NAMES[p.role]}: #${p.a} − #${p.b}`, color: ['#7966a8', '#c2577a', '#3b8fa3'][i % 3],
      filter: (r: Row) => r.address === -1 - i, value: (r: Row) => r.value
    })), step);
  });
  let hasDhi = $derived(rows.some(r => r.role === 'DHI'));
  let dniData = $derived(align(rows, [
    { label: 'DNI estimada (GHI − DHI)/cos z', color: '#7966a8', filter: r => r.role === 'DHI', value: r => r.dni }
  ], step));
  let ratio = $derived(align(rows, [
    { label: 'DHI / GHI', color: '#3b8fa3', filter: r => r.role === 'DHI', value: r => r.ratio }
  ], step));
  let temperature = $derived(align(rows, roleGroups(() => true, r => r.temp, false), step));
  let rtt = $derived(align(rows, roleGroups(() => true, r => r.rtt, false), step));
  let supply = $derived(align(rows, roleGroups(() => true, r => r.supply, false), step));

  const iso = (t: number) => new Date(t).toISOString();
  const age = (s: Sample) => Math.max(0, (tick - Date.parse(s.time_utc)) / 1000);
  const stale = (s: Sample) => age(s) > (data?.stale_s ?? 5);
  const readingState = (s: Sample) => !s.ok ? 'SIN RESPUESTA' : stale(s) ? 'ANTERIOR' : s.valid ? 'VÁLIDA' : 'INVÁLIDA';
  const failedTests = (s: Sample) => Object.entries(TEST_NAMES).filter(([k]) => s.qc[k as keyof typeof s.qc] === 'fail').map(([, v]) => v);

  async function loadNotes(start: number, end: number) {
    const result = await get<{ items: LogbookEntry[] }>(`/api/logbook${query({ start: iso(start), end: iso(end), include_voided: false })}`);
    if (result.ok) notes = result.value.items;
  }

  async function load() {
    const version = ++generation;
    closeStream();
    loading = true;
    const end = Date.now(), start = end - MONITOR_WINDOW_MS;
    const result = await get<{ items: Point[] }>(`/api/series${query({ start: iso(start), end: iso(end), resolution: '1s' })}`);
    if (version !== generation) return;
    loading = false;
    if (!result.ok) { error = result.error; return; }
    error = null;
    points = result.value.items;
    void loadNotes(start, end);
    connect();
  }

  function closeStream() { source?.close(); source = null; connected = false; streamKey = ''; }

  function connect() {
    if (!run || run.state !== 'running') return;
    const key = run.id;
    if (key === streamKey && source) return;
    closeStream();
    streamKey = key;
    const ids = [...points.filter(p => p.run_id === run!.id).map(p => p.id), ...latest.filter(s => s.run_id === run!.id).map(s => s.id)];
    const cursor = ids.length ? Math.max(...ids) : 0;
    const version = generation;
    source = new EventSource(`/api/runs/${run.id}/stream?after_id=${cursor}`);
    source.onopen = () => { if (version === generation) connected = true; };
    source.onerror = () => { if (version === generation) connected = false; };
    source.addEventListener('samples', event => {
      if (version !== generation) return;
      try {
        const page = JSON.parse((event as MessageEvent).data) as { items: Point[] };
        points = mergePoints(points, page.items, Date.now() - MONITOR_WINDOW_MS);
      } catch { error = { code: 'invalid_response', message: 'Evento de datos inválido' }; }
    });
    source.addEventListener('failure', () => { if (version === generation) { closeStream(); setTimeout(() => { if (version === generation) void load(); }, 3000); } });
  }

  // Nueva corrida o corrida que termina: reconectar o cerrar el stream.
  $effect(() => {
    const key = run && run.state === 'running' ? run.id : '';
    if (!key) { if (source) closeStream(); return; }
    if (key !== streamKey) connect();
  });

  onMount(() => {
    void load();
    // Sin datos nuevos (adquisición detenida) también se descarta lo que salió de la ventana.
    const clock = setInterval(() => {
      tick = Date.now();
      if (points.length && points[0].t_ms < tick - MONITOR_WINDOW_MS - 5000) points = mergePoints(points, [], tick - MONITOR_WINDOW_MS);
    }, 1000);
    const slow = setInterval(() => void loadNotes(Date.now() - MONITOR_WINDOW_MS, Date.now()), 60_000);
    return () => { generation++; clearInterval(clock); clearInterval(slow); closeStream(); };
  });
</script>

<svelte:head><title>Monitor · Radiometría</title></svelte:head>

<section class="page-head">
  <div>
    <div class="eyebrow">ADQUISICIÓN DE RADIACIÓN</div>
    <h1>Monitor de adquisición</h1>
    <p>Mediciones tal como las entrega cada sensor, con su rol, calidad y contexto. Los derivados (DNI estimada, cocientes, neta + σT⁴) se indican como tales.</p>
  </div>
  <div class="connection" class:active={running && connected}>
    <i></i>{running ? (connected ? 'En vivo' : 'Conectando…') : run ? 'Corrida terminada · histórico' : 'Esperando adquisición'}
  </div>
</section>

<div class="toolbar">
  <div class="run-meta">
    <span>Corrida <strong>{run?.metadata.config.label || 'sin etiqueta'}</strong></span>
    {#if run}<span>{localDate(run.started_utc)}</span><b class:sim={run.metadata.simulated}>{run.metadata.simulated ? 'SIMULADO' : 'HARDWARE'}</b><span>{format(interval, 1)} s / ciclo</span>{/if}
  </div>
  <a class="history-link" href="/historico/">Intervalos más largos: Histórico</a>
</div>

{#if live.error}<div class="notice error" role="alert"><strong>{live.error.code}</strong> · {live.error.message}{#if live.error.trace_id}<small>Traza: {live.error.trace_id}</small>{/if}</div>{/if}
{#if error}<div class="notice error" role="alert"><strong>{error.code}</strong> · {error.message}{#if error.trace_id}<small>Traza: {error.trace_id}</small>{/if}</div>{/if}
{#each data?.alerts ?? [] as alert}<div class="notice" class:error={alert.level === 'error'} role="alert">{alert.text}</div>{/each}
{#if data && !run}<div class="notice">Esperando la primera corrida. Inicie la adquisición para ver las mediciones.</div>{/if}

<section class="cards" aria-label="Mediciones principales">
  {#each latest as s (s.address)}
    <article class="value-card" class:old={stale(s)}>
      <div class="card-label"><span><i style:background={colorFor(s.address, addresses)}></i>{s.label} · {s.sensor_type === 'pyrgeometer' ? 'SGR4' : 'SMP22x'}</span><span>{readingState(s)}</span></div>
      <h2>{ROLE_NAMES[s.role]}</h2>
      <div class="reading">{format(s.valid ? s.measurement : null)}<span>W/m²</span></div>
      <footer>
        <span>Hace {format(age(s), 0)} s</span>
        <span class="qc {s.qc.summary}" title={[...failedTests(s), ...s.qc.notes].join(' · ')}>{QC_TEXT[s.qc.summary]}</span>
        {#if s.role === 'LWD'}<span>Neta: {format(s.net_longwave_wm2)} W/m²</span>{/if}
        {#if s.role === 'DHI'}<span>DHI/GHI {format(s.dhi_ghi_ratio, 2)} · DNI est. {format(s.dni_est_wm2, 0)} W/m²</span>{/if}
        {#if s.role === 'SHADE_OFF'}<span>SOLYS2 inactivo: no se presenta como difusa</span>{/if}
      </footer>
    </article>
  {/each}
  {#each data?.comparisons ?? [] as c}
    <article class="value-card compare">
      <div class="card-label"><span>COMPARACIÓN · #{c.a} / #{c.b}</span><span>{ROLE_NAMES[c.role]}</span></div>
      <h2>Diferencia relativa</h2>
      <div class="reading">{format(c.difference_pct, 2)}<span>%</span></div>
      <footer><span>{format(c.difference_wm2)} W/m²</span><small>Mismo ciclo, ambos válidos; % solo con media &gt; 100 W/m²</small></footer>
    </article>
  {/each}
  <article class="value-card info">
    <div class="card-label"><span>SOLYS2 Y RELOJ</span></div>
    <h2>Seguidor {data?.config.solys2_in_use ? 'en uso' : 'inactivo'}</h2>
    <div class="reading small">{data?.clock.check?.ok ? `${format(data.clock.check.offset_s, 3)} s` : '—'}<span>desfase del reloj</span></div>
    <footer><span>{data?.clock.check ? `NTP ${data.clock.check.server}` : data?.clock.enabled ? 'Sin comprobación todavía' : 'Comprobación deshabilitada'}</span><a href="/instrumentos/">Configurar</a></footer>
  </article>
</section>

<div class="section-heading"><h2>Señales y diagnósticos</h2><span>{loading ? 'Cargando…' : 'Último minuto · muestras de 1 s · hora local · arrastre para ampliar, doble clic para volver'}</span></div>
<section class="charts">
  <TimeChart title="Onda corta" unit="W/m²" data={shortwave} {markers} filename="onda-corta" subtitle="Valores del sensor sin corrección adicional. Cruces: alguna prueba de QC falló." />
  <TimeChart title="Onda larga descendente" unit="W/m²" data={longwave} {markers} filename="onda-larga" subtitle="Línea discontinua: neta + σT⁴, comprobación de consistencia (derivada)." />
  {#if pairs.length}<TimeChart title="Diferencia relativa entre instrumentos del mismo rol" unit="%" digits={2} data={comparison} {markers} filename="comparacion" subtitle="Denominador: media de ambos. Sin porcentaje con media ≤ 100 W/m²." />{/if}
  {#if hasDhi}
    <TimeChart title="Cociente difusa / global" unit="" digits={3} data={ratio} {markers} filename="cociente" subtitle="BSRN: DHI/GHI < 1,05 con cenit < 75°. Valores cercanos a 1 con cielo claro indican que la bola no tapa el sol." />
    <TimeChart title="DNI estimada" unit="W/m²" data={dniData} {markers} filename="dni-estimada" subtitle="Derivada (GHI − DHI)/cos z, solo con cenit < 80°. No es una medición de pirheliómetro." />
  {/if}
  <TimeChart title="Temperatura del cuerpo" unit="°C" data={temperature} {markers} filename="temperatura" subtitle="Temperatura interna del instrumento; no temperatura ambiente." />
  <TimeChart title="Tiempo de comunicación" unit="ms" data={rtt} filename="rtt" subtitle="RTT de la consulta Modbus, distinto de la respuesta física del sensor." />
  <TimeChart title="Tensión de alimentación" unit="V" data={supply} filename="alimentacion" subtitle="Lectura en el conector de cada instrumento." />
</section>

<section>
  <div class="section-heading"><h2>Detalle de la última lectura</h2><span>Las consultas de un ciclo son secuenciales, no simultáneas</span></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Instrumento</th><th>Rol</th><th>Magnitud principal</th><th>Temperatura</th><th>RTT</th><th>Flags</th><th>Calidad</th><th>QC</th><th>Cenit</th><th>Hora local</th></tr></thead>
    <tbody>
      {#each latest as s (s.address)}
        <tr>
          <td><strong>{s.label}</strong> {s.sensor_type === 'pyrgeometer' ? 'SGR4' : 'SMP'}</td>
          <td>{ROLE_NAMES[s.role]}</td>
          <td>{format(s.measurement)} W/m²</td>
          <td>{format(s.body_temp_c)} °C</td>
          <td>{format(s.rtt_ms)} ms</td>
          <td title={s.error || 'Máscara de estado'}>{s.status === null ? '—' : `0x${s.status.toString(16).padStart(4, '0')}`}</td>
          <td><span class="quality" class:bad={!s.valid || stale(s)}>{!s.ok ? 'Sin respuesta' : stale(s) ? 'Desactualizada' : s.valid ? 'Válida' : 'Inválida'}</span>{#if s.error}<small class="sample-error">{s.error}</small>{/if}</td>
          <td><span class="qc {s.qc.summary}">{QC_TEXT[s.qc.summary]}</span>{#if failedTests(s).length}<small class="sample-error">Falla: {failedTests(s).join(', ')}</small>{/if}{#each s.qc.notes as note}<small class="note">{note}</small>{/each}</td>
          <td>{s.solar_zenith_deg === null ? '—' : `${format(s.solar_zenith_deg, 1)}°`}</td>
          <td>{localDate(s.time_utc)}</td>
        </tr>
      {:else}<tr><td colspan="10" class="table-empty">No hay lecturas todavía.</td></tr>{/each}
    </tbody>
  </table></div>
</section>
<div class="bottom"><span>Radiometría · SMP22x + SGR4 · SOLYS2</span><span>Ciclo {run?.status.cycle ?? '—'} · Puerto perdido {run?.status.port_errors ?? 0} veces · {rows.length} puntos en pantalla</span></div>

<style>
  .connection { font-size: 11px; white-space: nowrap; color: #87999d; display: flex; align-items: center; gap: 8px; }
  .connection i { width: 7px; height: 7px; border-radius: 50%; background: #9aabad; }
  .connection.active { color: #31826f; }
  .connection.active i { background: #3a9f80; box-shadow: 0 0 0 4px #dfefe7; }
  .toolbar { background: #eaf0ed; border: 1px solid #dbe4df; border-radius: 7px; padding: 10px 14px; display: flex; align-items: center; gap: 16px; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; }
  .run-meta { display: flex; align-items: center; gap: 14px; font-size: 11px; color: #6f857b; flex-wrap: wrap; }
  .run-meta b { background: #e4e9d4; color: #6b743a; padding: 3px 6px; border-radius: 3px; font-size: 9px; letter-spacing: .8px; font-weight: 600; }
  .run-meta b.sim { background: #efe4cf; color: #8a6a2b; }
  .history-link { font-size: 11px; color: #2f6f62; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
  .value-card { background: #fff; border: 1px solid #dce3e3; border-radius: 9px; padding: 16px; }
  .card-label { display: flex; justify-content: space-between; gap: 6px; font-size: 9px; color: #657f83; }
  .card-label i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
  .card-label > span + span { font-size: 8px; color: #8c9d99; letter-spacing: .5px; }
  .value-card h2 { font-size: 11px; font-weight: 400; margin: 14px 0 6px; color: #7d9396; }
  .reading { font-size: 30px; letter-spacing: -1.2px; font-variant-numeric: tabular-nums; }
  .reading.small { font-size: 22px; }
  .reading > span { font-size: 11px; margin-left: 8px; color: #879c9e; letter-spacing: 0; }
  .value-card footer { font-size: 9px; color: #7f9390; margin-top: 12px; display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; align-items: center; }
  .value-card footer small { display: block; font-size: 8px; width: 100%; }
  .value-card footer a { color: #2f6f62; }
  .old .reading { color: #98a3a4; }
  .compare { background: #f0f2eb; border-color: #dce2d3; }
  .info { background: #f2f5f6; }
  .charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
  .quality { border-radius: 4px; background: #e9f3eb; color: #5a8c69; padding: 3px 7px; font-size: 9px; }
  .quality.bad { background: #fff1d9; color: #9a7b41; }
  .sample-error { display: block; font-size: 9px; color: #a06551; margin-top: 6px; max-width: 220px; white-space: normal; }
  .note { display: block; font-size: 9px; color: #7d8f92; margin-top: 4px; max-width: 220px; white-space: normal; }
  .table-empty { text-align: center; padding: 26px; }
  .bottom { display: flex; justify-content: space-between; color: #98a9a7; font-size: 9px; padding: 20px 0; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 1100px) { .charts { grid-template-columns: 1fr; } }
</style>

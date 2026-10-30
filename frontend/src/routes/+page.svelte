<script lang="ts">
  import { onMount } from 'svelte';
  import Chart from '$lib/Chart.svelte';
  import { get, format, mergeRows, makeSeries, differenceSeries, type Run, type Sample, type Status, type Page, type ApiError } from '$lib/data';

  let runs = $state<Run[]>([]), selected = $state(''), rows = $state<Sample[]>([]);
  let status = $state<Status | null>(null), error = $state<ApiError | null>(null);
  let connected = $state(false), loading = $state(false), tick = $state(Date.now());
  let source: EventSource | null = null, generation = 0, disposed = false;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  const date = (s: string) => new Date(s).toLocaleString('es', { dateStyle: 'short', timeStyle: 'medium' });
  let current = $derived(runs.find(r => r.id === selected));
  let latest = $derived(status?.latest ?? []);
  let interval = $derived(current?.metadata.config.interval_s ?? 1);
  let short = $derived([...new Set(rows.filter(r => r.sensor_type === 'pyranometer').map(r => r.address))]);
  let long = $derived([...new Set(rows.filter(r => r.sensor_type === 'pyrgeometer').map(r => r.address))]);
  let all = $derived([...new Set(rows.map(r => r.address))]);
  let live = $derived(status?.run.state === 'running' && !status?.acquisition_stale && connected);
  let windowRows = $derived.by(() => {
    if (!rows.length) return [];
    const end = Math.max(...rows.map(r => Date.parse(r.time_utc)));
    return rows.filter(r => Date.parse(r.time_utc) >= end - 15 * 60 * 1000);
  });
  let longSeries = $derived([
    ...makeSeries(windowRows, long, 'lw_down', interval, 'Ld↓ #'),
    ...makeSeries(windowRows, long, 'lw_down_calc', interval, 'Neta + σT⁴ #').map(s => ({ ...s, color: '#8aa2b7', dashed: true }))
  ]);
  const age = (s: Sample) => Math.max(0, (tick - Date.parse(s.time_utc)) / 1000);
  const stale = (s: Sample) => age(s) > (status?.stale_s ?? 5);
  const sensor = (address: number) => latest.find(s => s.address === address);

  async function updateStatus(id: string, version: number) {
    const result = await get<Status>(`/api/runs/${id}/status`);
    if (disposed || version !== generation) return null;
    if (result.ok) { status = result.value; return result.value; }
    else error = result.error;
    return null;
  }

  async function selectRun(id: string) {
    source?.close(); source = null; clearTimeout(retryTimer);
    const version = ++generation;
    selected = id; rows = []; status = null; connected = false; loading = true; error = null;
    const result = await get<Page>(`/api/runs/${id}/samples?recent=true&limit=3600`);
    if (disposed || version !== generation) return;
    loading = false;
    if (!result.ok) {
      error = result.error;
      retryTimer = setTimeout(() => { if (!disposed && version === generation) void selectRun(id); }, 3000);
      return;
    }
    rows = result.value.items;
    const fresh = await updateStatus(id, version);
    if (disposed || version !== generation || fresh?.run.state !== 'running') return;
    source = new EventSource(`/api/runs/${id}/stream?after_id=${result.value.next_cursor}`);
    source.onopen = () => { if (version === generation) { connected = true; error = null; } };
    source.onerror = () => { if (version === generation) connected = false; };
    source.addEventListener('samples', event => {
      if (version !== generation || disposed) return;
      try { rows = mergeRows(rows, (JSON.parse((event as MessageEvent).data) as Page).items); }
      catch { error = { code: 'invalid_response', message: 'Evento de datos inválido' }; }
    });
    source.addEventListener('failure', event => {
      if (version !== generation) return;
      const data = JSON.parse((event as MessageEvent).data);
      error = { code: data.error, message: 'La adquisición no está disponible', trace_id: data.trace_id };
      connected = false;
      source?.close();
      retryTimer = setTimeout(() => { if (!disposed && version === generation) void selectRun(id); }, 3000);
    });
  }

  onMount(() => {
    let timer: ReturnType<typeof setTimeout>;
    let iteration = 0;
    async function refresh() {
      tick = Date.now();
      if (iteration++ % 5 === 0 || !runs.length) {
        const result = await get<{ items: Run[] }>('/api/runs');
        if (disposed) return;
        if (result.ok) {
          runs = result.value.items;
          if (!selected && runs.length) void selectRun(runs[0].id);
          if (error?.code === 'network_error' || error?.code === 'database_unavailable') error = null;
        } else error = result.error;
      }
      if (selected) await updateStatus(selected, generation);
      if (!disposed) timer = setTimeout(refresh, 1000);
    }
    void refresh();
    const clock = setInterval(() => { tick = Date.now(); }, 1000);
    return () => { disposed = true; generation++; clearTimeout(timer); clearTimeout(retryTimer); clearInterval(clock); source?.close(); };
  });
</script>

<svelte:head>
  <title>Radiometría · Observatorio local</title>
  <meta name="description" content="Adquisición y seguimiento local de radiación de onda corta y larga" />
</svelte:head>

<div class="app">
  <aside>
    <div class="brand"><span class="brand-icon">☼</span><div>RADIOMETRÍA<small>OBSERVATORIO LOCAL</small></div></div>
    <div class="section-label">ESPACIO DE TRABAJO</div>
    <div class="nav-active"><span>◉</span> Monitor de adquisición</div>
    <div class="side-description">Corridas e históricos<br />SMP22x / SGR4</div>
    <div class="equipment"><div class="section-label">INSTRUMENTOS</div><p><i class="blue"></i>#2 · SGR4<small>Onda larga · pirgeómetro</small></p><p><i class="orange"></i>#3 · SMP22x<small>Onda corta · piranómetro</small></p><p><i class="green"></i>#4 · SMP22x<small>Onda corta · piranómetro</small></p></div>
    <div class="local"><span>●</span> Datos en este equipo<small>SQLite · sin servicios externos</small></div>
  </aside>

  <main>
    <header><div class="breadcrumb">Laboratorio <span>/</span> Monitor</div><span class="local-pill">LOCAL · SOLO LECTURA</span></header>
    <section class="title-row"><div><div class="eyebrow">ADQUISICIÓN DE RADIACIÓN</div><h1>Monitor de adquisición</h1><p>Mediciones de onda corta y larga, con su contexto y calidad.</p></div><div class:active={live} class="connection"><i></i>{live ? 'En vivo' : status?.run.state === 'running' ? 'Reconectando / sin datos recientes' : 'Histórico / en espera'}</div></section>

    <div class="toolbar"><label for="run">CORRIDA<select id="run" value={selected} onchange={event => void selectRun(event.currentTarget.value)}><option value="" disabled>Seleccionar corrida</option>{#each runs as run}<option value={run.id}>{run.metadata.config.label || 'Sin etiqueta'} · {date(run.started_utc)}</option>{/each}</select></label><div class="run-meta"><span>{current ? date(current.started_utc) : 'Sin corrida seleccionada'}</span><strong>{current?.metadata.simulated ? 'SIMULADO' : current ? 'HARDWARE' : 'EN ESPERA'}</strong><span>{format(interval, 1)} s / ciclo</span></div></div>

    {#if error}<div class="notice error" role="alert"><strong>{error.code}</strong> · {error.message}{#if error.trace_id}<small>Traza: {error.trace_id}</small>{/if}</div>{/if}
    {#if status?.acquisition_stale}<div class="notice" role="alert">La adquisición no confirma actividad reciente. Los valores visibles pueden estar desactualizados.</div>{/if}
    {#if !runs.length && !error}<div class="notice">Esperando la primera corrida. Inicia la adquisición para ver las mediciones.</div>{/if}

    <section class="cards" aria-label="Mediciones principales">
      {#each [3, 4, 2] as address}
        {@const s = sensor(address)}
        <article class="value-card" class:old={s && stale(s)}><div class="card-label"><span><i class:blue={address === 2} class:orange={address === 3} class:green={address === 4}></i>#{address} · {address === 2 ? 'SGR4' : 'SMP22x'}</span><span>{s ? !s.ok ? 'SIN RESPUESTA' : stale(s) ? 'ANTERIOR' : s.valid ? 'VÁLIDA' : 'INVÁLIDA' : 'SIN DATOS'}</span></div><h2>{address === 2 ? 'Onda larga descendente' : 'Irradiancia de onda corta'}</h2><div class="reading">{format(s?.valid ? s.measurement : null)}<span>W/m²</span></div><footer>{s ? `Última lectura hace ${format(age(s), 0)} s` : 'Esperando lectura'}{#if address === 2}<span>Neta: {format(s?.net_longwave_wm2)} W/m²</span>{/if}</footer></article>
      {/each}
      <article class="value-card compare"><div class="card-label"><span>COMPARACIÓN · #3 / #4</span></div><h2>Diferencia relativa</h2><div class="reading">{format(status?.comparison.difference_pct, 2)}<span>%</span></div><footer>{format(status?.comparison.difference_wm2)} W/m²<small>Solo pares válidos; media &gt;100 W/m²</small></footer></article>
    </section>

    <div class="section-heading"><h2>Señales y diagnósticos</h2><span>{loading ? 'Cargando…' : 'Ventana de hasta 15 min · máximo 3600 muestras'} · hora local</span></div>
    <section class="charts">
      <Chart title="Irradiancia de onda corta" unit="W/m²" series={makeSeries(windowRows, short, 'shortwave_wm2', interval)} subtitle="Valores del sensor en unidades físicas; sin corrección adicional." />
      <Chart title="Onda larga descendente" unit="W/m²" series={longSeries} subtitle="Línea discontinua: neta + σT⁴. Comprobación de consistencia." />
      <Chart title="Diferencia relativa #3 − #4" unit="%" series={differenceSeries(windowRows)} subtitle="Denominador: media de ambos. Sin porcentaje cerca de cero." />
      <Chart title="Temperatura del cuerpo" unit="°C" series={makeSeries(windowRows, all, 'body_temp_c', interval)} subtitle="Temperatura interna del instrumento; no temperatura ambiente." />
      <Chart title="Tiempo de comunicación" unit="ms" series={makeSeries(windowRows, all, 'rtt_ms', interval)} subtitle="RTT de la consulta Modbus, distinto de la respuesta física del sensor." />
      <Chart title="Tensión de alimentación" unit="V" series={makeSeries(windowRows, all, 'supply_v', interval)} subtitle="Lectura en el conector de cada instrumento." />
    </section>

    <section class="measurements"><div class="section-heading"><h2>Detalle de la última lectura</h2><span>Las consultas de un ciclo son secuenciales</span></div><div class="table-wrap"><table><thead><tr><th>Sensor</th><th>Magnitud principal</th><th>Temperatura</th><th>RTT</th><th>Flags</th><th>Calidad</th><th>Hora local</th></tr></thead><tbody>{#each latest as s}<tr><td><strong>#{s.address}</strong> {s.sensor_type === 'pyrgeometer' ? 'SGR4' : 'SMP'}</td><td>{format(s.measurement)} W/m²</td><td>{format(s.body_temp_c)} °C</td><td>{format(s.rtt_ms)} ms</td><td title={s.error || 'Máscara de estado'}>{s.status === null ? '—' : `0x${s.status.toString(16).padStart(4, '0')}`}</td><td><span class="quality" class:bad={!s.valid || stale(s)}>{!s.ok ? 'Sin respuesta' : stale(s) ? 'Desactualizada' : s.valid ? 'Válida' : 'Inválida'}</span>{#if s.error}<small class="sample-error">{s.error}</small>{/if}</td><td>{date(s.time_utc)}</td></tr>{:else}<tr><td colspan="7" class="table-empty">{loading ? 'Cargando lecturas…' : 'No hay lecturas para esta corrida.'}</td></tr>{/each}</tbody></table></div></section>
    <div class="bottom"><span>Radiometría · SMP22x + SGR4</span><span>Ciclo {status?.run.status.cycle ?? '—'} · Puerto perdido {status?.run.status.port_errors ?? 0} veces · {rows.length} muestras en memoria</span></div>
  </main>
</div>

<style>
  :global(*) { box-sizing: border-box; } :global(body) { margin: 0; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #f3f6f5; color: #223d44; font-size: 13px; }
  :global(button), :global(select) { font: inherit; } :global(:focus-visible) { outline: 2px solid #3a9b8c; outline-offset: 3px; }
  .app { display: flex; min-height: 100vh; } aside { width: 226px; background: #142a31; color: #e2eeee; padding: 32px 20px; position: fixed; inset: 0 auto 0 0; display: flex; flex-direction: column; }
  .brand { display: flex; gap: 12px; align-items: center; font-size: 13px; font-weight: 700; letter-spacing: 1.3px; margin-bottom: 55px; }.brand-icon { font-size: 33px; color: #bdcf85; font-weight: 400; }.brand small { display: block; font-size: 8px; letter-spacing: 1.9px; color: #93a9ad; margin-top: 5px; }
  .section-label { color: #759197; font-size: 9px; letter-spacing: 1.4px; margin-bottom: 16px; }.nav-active { background: #244149; border: 1px solid #34535a; border-radius: 6px; margin: 0 -5px; padding: 13px 10px; font-size: 12px; }.nav-active span { color: #abd3b4; padding-right: 6px; }.side-description { margin: 16px 8px; color: #7f9a9f; font-size: 11px; line-height: 1.8; }.equipment { border-top: 1px solid #29434a; padding-top: 28px; margin-top: 28px; }.equipment p { font-size: 12px; padding-bottom: 14px; }.equipment small { display: block; color: #819ca1; font-size: 10px; padding: 6px 0 0 15px; }
  i { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 7px; }.blue { background: #447ec4; }.orange { background: #d67c2e; }.green { background: #1d8a76; }.local { margin-top: auto; padding-top: 40px; font-size: 10px; color: #b8ced1; }.local>span { color: #80b49e; padding-right: 5px; }.local small { display: block; color: #67858c; font-size: 9px; margin-top: 7px; }
  main { margin-left: 226px; padding: 0 36px; width: calc(100% - 226px); max-width: 1850px; }header { height: 66px; border-bottom: 1px solid #dfe7e5; display: flex; align-items: center; justify-content: space-between; }.breadcrumb { font-size: 11px; color: #879b9e; }.breadcrumb span { padding: 0 12px; color: #b4c1c2; }.local-pill { border: 1px solid #dce5e2; padding: 5px 9px; border-radius: 4px; font-size: 8px; letter-spacing: 1px; color: #6b8586; }
  .title-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 30px 0 24px; }.eyebrow { color: #82958f; font-size: 9px; letter-spacing: 1.8px; }h1 { font-size: 29px; font-weight: 550; letter-spacing: -.9px; margin: 8px 0; }.title-row p { color: #819498; font-size: 12px; margin: 0; }.connection { font-size: 10px; white-space: nowrap; color: #87999d; }.connection i { background: #9aabad; }.connection.active { color: #31826f; }.connection.active i { background: #3a9f80; box-shadow: 0 0 0 4px #dfefe7; }
  .toolbar { background: #eaf0ed; border: 1px solid #dbe4df; border-radius: 7px; padding: 13px 16px; display: flex; align-items: center; gap: 20px; justify-content: space-between; margin-bottom: 22px; }label { font-size: 8px; color: #83978e; letter-spacing: 1px; display: flex; align-items: center; gap: 12px; } select { background: transparent; border: none; max-width: 340px; color: #315047; font-size: 11px; padding: 5px; }.run-meta { display: flex; align-items: center; gap: 16px; font-size: 10px; color: #7a8f85; }.run-meta strong { background: #e4e9d4; color: #7d863f; padding: 4px 6px; border-radius: 3px; font-size: 8px; letter-spacing: .8px; }
  .notice { border: 1px solid #e0d5b2; background: #fff9e9; color: #827140; border-radius: 6px; padding: 12px 16px; margin-bottom: 18px; font-size: 12px; }.notice.error { background: #fff2ef; border-color: #efd7d0; color: #a25f4e; }.notice small { display: block; margin-top: 5px; font-family: monospace; }
  .cards { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 14px; }.value-card { background: #fff; border: 1px solid #dce3e3; border-radius: 9px; padding: 18px; }.card-label { display: flex; justify-content: space-between; gap: 4px; font-size: 9px; color: #657f83; }.card-label>span+span { font-size: 7px; color: #8c9d99; }.value-card h2 { font-size: 11px; font-weight: 400; margin: 16px 0 8px; color: #82989b; }.reading { font-size: 31px; letter-spacing: -1.2px; font-variant-numeric: tabular-nums; }.reading>span { font-size: 11px; margin-left: 8px; color: #879c9e; letter-spacing: 0; }footer { font-size: 8px; color: #8da09e; margin-top: 14px; display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; }footer small { display: block; font-size: 8px; }.old .reading { color: #98a3a4; }.compare { background: #f0f2eb; border-color: #dce2d3; }.compare .reading { color: #657357; }
  .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 15px; padding: 26px 0 14px; }.section-heading h2 { font-size: 14px; font-weight: 600; margin: 0; }.section-heading span { font-size: 9px; color: #8a9c9e; }.charts { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 16px; }.table-wrap { border: 1px solid #dce3e3; border-radius: 8px; overflow: auto; background: white; }table { width: 100%; border-collapse: collapse; font-size: 10px; text-align: left; white-space: nowrap; }th { color: #81969b; font-size: 9px; font-weight: 500; background: #f9fbfa; padding: 13px 16px; }td { padding: 16px; border-top: 1px solid #edf1ef; color: #6e8589; font-variant-numeric: tabular-nums; }td strong { color: #345158; margin-right: 4px; }.quality { border-radius: 4px; background: #e9f3eb; color: #5a8c69; padding: 4px 7px; font-size: 9px; }.quality.bad { background: #fff1d9; color: #9a7b41; }.sample-error { display: block; font-size: 9px; color: #a06551; margin-top: 7px; max-width: 200px; white-space: normal; }.table-empty { text-align: center; padding: 30px; }.bottom { display: flex; justify-content: space-between; color: #98a9a7; font-size: 9px; padding: 24px 0; gap: 10px; }
  @media(min-width:1600px) { main { padding: 0 55px; } }@media(max-width:1150px) { aside { width: 190px; padding-inline: 14px; }main { margin-left: 190px; width: calc(100% - 190px); padding: 0 22px; }.cards { grid-template-columns: repeat(2,minmax(0,1fr)); }.run-meta>span:first-child { display: none; } }@media(max-width:760px) { aside { display: none; }main { margin-left: 0; width: 100%; padding: 0 16px; }.charts { grid-template-columns: 1fr; }.title-row { align-items: flex-start; flex-direction: column; gap: 12px; }h1 { font-size: 25px; }.toolbar { align-items: flex-start; flex-direction: column; gap: 8px; }select { max-width: 240px; }.section-heading { align-items: flex-start; flex-direction: column; gap: 6px; }.bottom { flex-direction: column; }.value-card { padding: 13px; }.reading { font-size: 26px; }.card-label { flex-direction: column; } }
</style>

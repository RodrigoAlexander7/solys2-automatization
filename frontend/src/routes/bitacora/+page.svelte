<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, query, type ApiError, type LogbookEntry, type TimelineItem } from '$lib/api';
  import { live, rememberAuthor, savedAuthor } from '$lib/live.svelte';
  import { fromInputValue, localDate, toInputValue } from '$lib/data';

  const CATEGORIES: Record<string, string> = {
    limpieza: 'Limpieza de domo', nivelacion: 'Nivelación', sombra: 'Sombra u obstrucción', desecante: 'Desecante',
    mantenimiento: 'Mantenimiento', solys2: 'SOLYS2', configuracion: 'Configuración', observacion: 'Observación', otro: 'Otro'
  };
  const SOURCES = [{ key: 'adquisicion', label: 'Adquisición' }, { key: 'bitacora', label: 'Bitácora' }, { key: 'configuracion', label: 'Configuración' }];
  const DAY = 86_400_000;

  let author = $state(''), when = $state(''), category = $state('limpieza'), text = $state('');
  let chosen = $state<number[]>([]);
  let sources = $state(['adquisicion', 'bitacora', 'configuracion']);
  let severity = $state<'all' | 'warning'>('all');
  let fromText = $state(''), toText = $state('');
  let items = $state<TimelineItem[]>([]);
  let error = $state<ApiError | null>(null), saving = $state(false), notice = $state('');

  let known = $derived([...new Set([...(live.data?.config.instruments.map(i => i.address) ?? []), ...(live.data?.latest.map(s => s.address) ?? [])])].sort((a, b) => a - b));
  let visible = $derived(items.filter(i => severity === 'all' || i.severity !== 'info'));
  let byDay = $derived.by(() => {
    const groups = new Map<string, TimelineItem[]>();
    for (const item of visible) {
      const day = new Date(item.t_ms).toLocaleDateString('es', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
      groups.set(day, [...(groups.get(day) ?? []), item]);
    }
    return [...groups.entries()];
  });

  async function load() {
    const start = fromInputValue(fromText), end = fromInputValue(toText);
    if (start === null || end === null || start >= end) { error = { code: 'invalid_input', message: 'Intervalo inválido' }; return; }
    const result = await get<{ items: TimelineItem[] }>(`/api/timeline${query({
      start: new Date(start).toISOString(), end: new Date(end).toISOString(), sources: sources.join(',') })}`);
    if (result.ok) { items = result.value.items; error = null; } else error = result.error;
  }

  async function add(event: SubmitEvent) {
    event.preventDefault();
    saving = true; notice = '';
    rememberAuthor(author);
    const t = when ? fromInputValue(when) : null;
    const result = await post<LogbookEntry>('/api/logbook', {
      author, category, text, addresses: chosen, time_utc: t ? new Date(t).toISOString() : null });
    saving = false;
    if (!result.ok) { error = result.error; return; }
    text = ''; when = ''; chosen = []; notice = 'Nota registrada.';
    toText = toInputValue(Date.now() + 60_000);
    await load();
  }

  async function voidEntry(entry: LogbookEntry) {
    const reason = prompt('Motivo para anular la nota (no se borra, queda marcada):');
    if (!reason?.trim()) return;
    if (!author.trim()) { error = { code: 'invalid_input', message: 'Indique su nombre en «Autor» antes de anular' }; return; }
    const result = await post<LogbookEntry>(`/api/logbook/${entry.id}/void`, { author, reason });
    if (!result.ok) { error = result.error; return; }
    await load();
  }

  function toggle(address: number) {
    chosen = chosen.includes(address) ? chosen.filter(a => a !== address) : [...chosen, address];
  }

  function toggleSource(key: string) {
    sources = sources.includes(key) ? sources.filter(s => s !== key) : [...sources, key];
    void load();
  }

  onMount(() => {
    author = savedAuthor();
    fromText = toInputValue(Date.now() - 7 * DAY);
    toText = toInputValue(Date.now() + 60_000);
    void load();
  });
</script>

<svelte:head><title>Bitácora · Radiometría</title></svelte:head>

<section class="page-head">
  <div>
    <div class="eyebrow">REGISTRO DEL OBSERVATORIO</div>
    <h1>Bitácora y eventos</h1>
    <p>Anote limpieza de domos, nivelación, sombras o mantenimiento. Las notas aparecen como marcas en los gráficos y viajan en las descargas. Debajo, la línea de tiempo une las notas con los eventos de adquisición (flags, puerto, reloj, archivo) y los cambios de configuración.</p>
  </div>
</section>

{#if error}<div class="notice error" role="alert"><strong>{error.code}</strong> · {error.message}{#if error.trace_id}<small>Traza: {error.trace_id}</small>{/if}</div>{/if}

<form class="card entry" onsubmit={add}>
  <div class="grid">
    <label class="field">Autor<input required maxlength="60" bind:value={author} placeholder="Nombre" /></label>
    <label class="field">Hora (vacío = ahora)<input type="datetime-local" bind:value={when} /></label>
    <label class="field">Categoría<select bind:value={category}>{#each Object.entries(CATEGORIES) as [key, label]}<option value={key}>{label}</option>{/each}</select></label>
  </div>
  <fieldset><legend>Instrumentos afectados (opcional)</legend>
    {#each known as address}<label class="check"><input type="checkbox" checked={chosen.includes(address)} onchange={() => toggle(address)} /> #{address}</label>{/each}
  </fieldset>
  <label class="field">Nota<textarea required rows="3" maxlength="2000" bind:value={text} placeholder="Qué se hizo u observó"></textarea></label>
  <div class="actions"><button class="btn primary" type="submit" disabled={saving || !author.trim() || !text.trim()}>{saving ? 'Guardando…' : 'Agregar a la bitácora'}</button>{#if notice}<span class="ok">{notice}</span>{/if}</div>
</form>

<div class="section-heading">
  <h2>Línea de tiempo</h2>
  <div class="filters">
    <input type="datetime-local" bind:value={fromText} aria-label="Desde" />
    <input type="datetime-local" bind:value={toText} aria-label="Hasta" />
    <button type="button" class="btn" onclick={() => void load()}>Actualizar</button>
    {#each SOURCES as s}<label class="check"><input type="checkbox" checked={sources.includes(s.key)} onchange={() => toggleSource(s.key)} /> {s.label}</label>{/each}
    <label class="check"><input type="checkbox" checked={severity === 'warning'} onchange={e => (severity = e.currentTarget.checked ? 'warning' : 'all')} /> Solo avisos y errores</label>
  </div>
</div>

{#each byDay as [day, entries]}
  <h3 class="day">{day}</h3>
  <ol class="timeline">
    {#each entries as item (item.source + item.t_ms + item.title + (item.entry?.id ?? ''))}
      <li class="{item.source} {item.severity}" class:voided={item.entry?.voided}>
        <time>{new Date(item.t_ms).toLocaleTimeString('es')}</time>
        <div>
          <strong>{item.title}</strong>
          <span class="source">{SOURCES.find(s => s.key === item.source)?.label}{item.address !== null ? ` · #${item.address}` : ''}</span>
          {#if item.entry}
            <p>{item.entry.text}</p>
            <small>{item.entry.author}{item.entry.addresses.length ? ` · instrumentos ${item.entry.addresses.map(a => `#${a}`).join(', ')}` : ''}{item.entry.source !== 'manual' ? ' · automática' : ''}</small>
            {#if item.entry.voided}<small class="voidinfo">Anulada por {item.entry.voided_by} ({localDate(item.entry.voided_at!)}): {item.entry.void_reason}</small>
            {:else if item.entry.source === 'manual'}<button type="button" class="link" onclick={() => voidEntry(item.entry!)}>Anular</button>{/if}
          {/if}
          {#if item.version}<p>{item.version.note || 'Sin nota'}</p>{/if}
        </div>
      </li>
    {/each}
  </ol>
{:else}<div class="notice">No hay eventos ni notas en el intervalo elegido.</div>{/each}

<style>
  .entry { display: flex; flex-direction: column; gap: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  fieldset { border: 1px solid #e1e8e6; border-radius: 6px; padding: 8px 12px; display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; }
  legend { font-size: 11px; color: #5d7477; padding: 0 4px; }
  .check { display: flex; gap: 6px; align-items: center; font-size: 11px; color: #4f676a; }
  .actions { display: flex; gap: 12px; align-items: center; }
  .ok { color: #3f7656; font-size: 12px; }
  .filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .filters input[type='datetime-local'] { border: 1px solid #d3dedb; border-radius: 6px; padding: 5px 7px; font-size: 11px; }
  .day { font-size: 12px; color: #5c7377; font-weight: 600; margin: 18px 0 8px; text-transform: capitalize; }
  .timeline { list-style: none; margin: 0; padding: 0; border-left: 2px solid #dbe4e1; }
  .timeline li { display: grid; grid-template-columns: 80px 1fr; gap: 10px; padding: 9px 12px; margin-left: -2px; border-left: 3px solid #b9c9c6; background: #fff; border-bottom: 1px solid #eef2f0; }
  .timeline li.bitacora { border-left-color: #3a8f7c; }
  .timeline li.configuracion { border-left-color: #6b6fb0; }
  .timeline li.warning { border-left-color: #c9952f; background: #fffbf2; }
  .timeline li.error { border-left-color: #c2533f; background: #fff6f4; }
  .timeline li.voided { opacity: .55; }
  .timeline li.voided strong { text-decoration: line-through; }
  time { font-size: 11px; color: #6f8487; font-variant-numeric: tabular-nums; }
  strong { font-size: 12px; font-weight: 600; color: #2b474c; }
  .source { font-size: 10px; color: #8a9c9e; margin-left: 8px; }
  p { margin: 4px 0; font-size: 12px; color: #4b6366; white-space: pre-wrap; }
  small { display: block; font-size: 10px; color: #7d9093; }
  .voidinfo { color: #9a5d4e; }
  .link { background: none; border: none; color: #8a5a44; font-size: 10px; padding: 0; cursor: pointer; text-decoration: underline; margin-top: 4px; }
</style>

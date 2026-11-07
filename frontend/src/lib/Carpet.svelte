<script lang="ts">
  import { format } from './data';

  type Cell = { minute_ms: number; mean_wm2: number | null };
  let { rows, startMs, endMs, title, unit = 'W/m²' }:
    { rows: Cell[]; startMs: number; endMs: number; title: string; unit?: string } = $props();

  let canvas = $state<HTMLCanvasElement>();
  let hover = $state<string>('');
  const STOPS: [number, number, number][] = [[24, 38, 74], [36, 104, 142], [42, 157, 143], [138, 196, 104], [242, 216, 92]];

  // Días locales del intervalo: una fila por día, una columna por minuto del día.
  let days = $derived.by(() => {
    const first = new Date(startMs); first.setHours(0, 0, 0, 0);
    const list: number[] = [];
    for (let d = new Date(first); d.getTime() < endMs; d.setDate(d.getDate() + 1)) list.push(d.getTime());
    return list;
  });
  let range = $derived.by(() => {
    const values = rows.map(r => r.mean_wm2).filter((v): v is number => v !== null && Number.isFinite(v));
    if (!values.length) return null;
    return { min: Math.min(0, ...values.slice(0, 200000)), max: Math.max(...values.slice(0, 200000)) };
  });

  function color(value: number, min: number, max: number): [number, number, number] {
    const f = Math.min(1, Math.max(0, (value - min) / Math.max(1e-9, max - min))) * (STOPS.length - 1);
    const i = Math.min(STOPS.length - 2, Math.floor(f)), w = f - i;
    return STOPS[i].map((c, k) => Math.round(c + (STOPS[i + 1][k] - c) * w)) as [number, number, number];
  }

  function locate(t: number): [number, number] | null {
    const d = new Date(t);
    const day = new Date(d); day.setHours(0, 0, 0, 0);
    const row = days.indexOf(day.getTime());
    return row < 0 ? null : [d.getHours() * 60 + d.getMinutes(), row];
  }

  // Celda (fila·1440 + minuto del día) → valor medio.
  let cells = $derived.by(() => {
    const map = new Map<number, number>();
    for (const r of rows) {
      if (r.mean_wm2 === null) continue;
      const cell = locate(r.minute_ms);
      if (cell) map.set(cell[1] * 1440 + cell[0], r.mean_wm2);
    }
    return map;
  });

  $effect(() => {
    if (!canvas || !days.length) return;
    canvas.width = 1440;
    canvas.height = days.length;
    const ctx = canvas.getContext('2d')!;
    const image = ctx.createImageData(1440, days.length);
    for (let k = 0; k < image.data.length; k += 4) { image.data[k] = 236; image.data[k + 1] = 240; image.data[k + 2] = 239; image.data[k + 3] = 255; }
    if (range) for (const [index, value] of cells) {
      const [cr, cg, cb] = color(value, range.min, range.max);
      const k = index * 4;
      image.data[k] = cr; image.data[k + 1] = cg; image.data[k + 2] = cb;
    }
    ctx.putImageData(image, 0, 0);
  });

  function onmove(event: MouseEvent) {
    if (!canvas) return;
    const box = canvas.getBoundingClientRect();
    const x = Math.floor((event.clientX - box.left) / box.width * 1440);
    const y = Math.floor((event.clientY - box.top) / box.height * days.length);
    if (x < 0 || x >= 1440 || y < 0 || y >= days.length) { hover = ''; return; }
    const value = cells.get(y * 1440 + x);
    const t = days[y] + x * 60_000;
    hover = `${new Date(t).toLocaleString('es', { dateStyle: 'short', timeStyle: 'short' })} · ${value !== undefined ? `${format(value)} ${unit}` : 'sin dato'}`;
  }
</script>

<article class="carpet">
  <div class="heading"><h3>{title}</h3><span>{hover || 'Día × hora local · promedios de 1 min'}</span></div>
  {#if days.length && range}
    <div class="frame">
      <div class="days"><span>{new Date(days[0]).toLocaleDateString('es')}</span><span>{new Date(days.at(-1)!).toLocaleDateString('es')}</span></div>
      <div class="plot">
        <canvas bind:this={canvas} onmousemove={onmove} onmouseleave={() => (hover = '')} style:height={`${Math.min(420, Math.max(60, days.length * 14))}px`} aria-label={title}></canvas>
        <div class="hours">{#each [0, 3, 6, 9, 12, 15, 18, 21, 24] as h}<span style:left={`${h / 24 * 100}%`}>{h}</span>{/each}</div>
      </div>
    </div>
    <div class="scale"><span>{format(range.min, 0)}</span><i></i><span>{format(range.max, 0)} {unit}</span><small>Gris: sin dato (hueco, no cero)</small></div>
  {:else}
    <div class="empty">Sin promedios de minuto en el intervalo</div>
  {/if}
</article>

<style>
  .carpet { background: white; border: 1px solid #dce3e3; border-radius: 10px; padding: 16px; min-width: 0; }
  .heading { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  h3 { margin: 0 0 10px; font-size: 14px; font-weight: 600; color: #243e45; }
  .heading span { font-size: 11px; color: #6f8487; font-variant-numeric: tabular-nums; }
  .frame { display: grid; grid-template-columns: 74px 1fr; gap: 8px; }
  .days { display: flex; flex-direction: column; justify-content: space-between; font-size: 10px; color: #7a8d90; }
  .plot { position: relative; padding-bottom: 18px; }
  canvas { width: 100%; image-rendering: pixelated; display: block; border-radius: 4px; cursor: crosshair; }
  .hours { position: absolute; left: 0; right: 0; bottom: 0; height: 14px; font-size: 10px; color: #7a8d90; }
  .hours span { position: absolute; transform: translateX(-50%); }
  .scale { display: flex; align-items: center; gap: 8px; font-size: 10px; color: #6f8487; margin-top: 8px; flex-wrap: wrap; }
  .scale i { width: 160px; height: 8px; border-radius: 4px; background: linear-gradient(90deg, rgb(24,38,74), rgb(36,104,142), rgb(42,157,143), rgb(138,196,104), rgb(242,216,92)); }
  .scale small { margin-left: auto; }
  .empty { padding: 30px; text-align: center; color: #87999c; font-size: 12px; }
</style>

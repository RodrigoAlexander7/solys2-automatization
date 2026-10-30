<script lang="ts">
  import type { Series } from './data';
  import { format } from './data';
  let { title, unit, series, subtitle = '' }: { title: string; unit: string; series: Series[]; subtitle?: string } = $props();
  const width = 600, height = 172, left = 53, right = 14, top = 12, bottom = 26;
  let values = $derived(series.flatMap(s => s.points.filter(p => p.value !== null).map(p => p.value!)));
  let times = $derived(series.flatMap(s => s.points.map(p => p.time)).filter(Number.isFinite));
  let minY = $derived(values.length ? Math.min(...values) : 0);
  let maxY = $derived(values.length ? Math.max(...values) : 1);
  let pad = $derived(Math.max((maxY - minY) * .15, Math.abs(maxY) * .005, .5));
  let lo = $derived(minY - pad), hi = $derived(maxY + pad);
  let minT = $derived(times.length ? Math.min(...times) : 0);
  let maxT = $derived(times.length ? Math.max(...times) : 1);
  const x = (t: number) => left + (t - minT) / Math.max(1000, maxT - minT) * (width - left - right);
  const y = (v: number) => top + (hi - v) / (hi - lo) * (height - top - bottom);
  function paths(s: Series): string[] {
    const result: string[] = []; let current = '';
    for (const p of s.points) {
      if (p.value === null) { if (current) result.push(current); current = ''; }
      else current += `${current ? ' L' : 'M'}${x(p.time).toFixed(2)},${y(p.value).toFixed(2)}`;
    }
    if (current) result.push(current);
    return result;
  }
  const clock = (t: number) => new Date(t).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
</script>

<article class="chart">
  <div class="heading"><h3>{title}</h3><span>{unit}</span></div>
  <div class="legend">{#each series as s}<span><i style:background={s.color}></i>{s.label}</span>{/each}</div>
  {#if values.length}
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}, ${unit}`}>
      {#each [0, 1, 2] as tick}
        {@const v = lo + (hi - lo) * tick / 2}
        <line x1={left} x2={width - right} y1={y(v)} y2={y(v)} stroke="#e4e9e9" />
        <text x={left - 8} y={y(v) + 4} text-anchor="end">{format(v)}</text>
      {/each}
      {#each series as s}{#each paths(s) as d}
        <path {d} fill="none" stroke={s.color} stroke-width="1.8" stroke-linecap="round" stroke-dasharray={s.dashed ? '5 4' : undefined} />
      {/each}{/each}
      <text x={left} y={height - 3}>{clock(minT)}</text>
      <text x={width - right} y={height - 3} text-anchor="end">{clock(maxT)}</text>
    </svg>
  {:else}<div class="empty">Sin lecturas válidas en esta ventana</div>{/if}
  <p>{subtitle}</p>
</article>

<style>
  .chart { background: white; border: 1px solid #dce3e3; border-radius: 10px; padding: 20px 20px 12px; min-width: 0; }
  .heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
  h3 { margin: 0; color: #243e45; font-size: 14px; font-weight: 600; }
  .heading>span { color: #74878b; font-size: 11px; }
  .legend { display: flex; gap: 16px; min-height: 26px; align-items: center; font-size: 11px; color: #657b80; }
  .legend span { display: flex; gap: 5px; align-items: center; } i { width: 12px; height: 3px; border-radius: 2px; }
  svg { width: 100%; display: block; overflow: visible; } text { font: 10px system-ui; fill: #849497; }
  p { margin: 8px 0 0; color: #819195; font-size: 10px; min-height: 13px; }
  .empty { display: grid; place-items: center; height: 172px; color: #87999c; font-size: 12px; }
</style>

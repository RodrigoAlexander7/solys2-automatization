<script lang="ts">
  import { onMount } from 'svelte';
  import uPlot from 'uplot';
  import 'uplot/dist/uPlot.min.css';
  import { format, type Aligned } from './data';

  type Marker = { t_ms: number; label: string };
  let { title, unit, data, subtitle = '', markers = [], syncKey = 'radiometria', height = 190, digits = 1, filename = 'grafico' }:
    { title: string; unit: string; data: Aligned; subtitle?: string; markers?: Marker[]; syncKey?: string;
      height?: number; digits?: number; filename?: string } = $props();

  let host: HTMLDivElement;
  let plot: uPlot | null = null;
  let signature = '';
  let zoomed = $state(false);
  let hasValues = $derived(data.series.some(s => !s.pointsOnly && s.values.some(v => v !== null)));

  /** Índices de valores aislados (sin vecinos): una lectura válida entre huecos no debe quedar invisible. */
  function isolated(u: uPlot, seriesIdx: number): number[] {
    const values = u.data[seriesIdx] as (number | null)[];
    const out: number[] = [];
    for (let i = 0; i < values.length; i++)
      if (values[i] != null && values[i - 1] == null && values[i + 1] == null) out.push(i);
    return out;
  }

  function options(width: number): uPlot.Options {
    return {
      width, height,
      cursor: { sync: { key: syncKey, setSeries: false }, drag: { x: true, y: false }, focus: { prox: 16 } },
      select: { show: true, left: 0, top: 0, width: 0, height: 0 },
      scales: { x: { time: true } },
      legend: { live: true },
      axes: [
        { stroke: '#7d8f92', grid: { stroke: '#edf1f0', width: 1 }, ticks: { stroke: '#dfe6e4', width: 1 }, font: '10px system-ui' },
        { stroke: '#7d8f92', grid: { stroke: '#edf1f0', width: 1 }, ticks: { show: false }, size: 52, font: '10px system-ui',
          values: (_u, values) => values.map(v => format(v, Math.abs(v) < 10 ? 2 : digits)) }
      ],
      series: [
        { value: (_u, v) => v == null ? '—' : new Date(v * 1000).toLocaleString('es', { dateStyle: 'short', timeStyle: 'medium' }) },
        ...data.series.map((s): uPlot.Series => ({
          label: s.label, stroke: s.color, width: s.pointsOnly ? 0 : 1.6,
          ...(s.dashed ? { dash: [5, 4] } : {}),
          spanGaps: false,
          points: s.pointsOnly
            ? { show: true, size: 6, width: 1.5, stroke: s.color, fill: '#fff' }
            : { show: true, size: 4, width: 1, stroke: s.color, fill: s.color, filter: isolated },
          ...(s.pointsOnly ? { paths: (() => null) as unknown as uPlot.Series.PathBuilder } : {}),
          value: (_u: uPlot, v: number | null) => v == null ? '—' : `${format(v, digits)} ${unit}`
        }))
      ],
      hooks: {
        draw: [u => {
          if (!markers.length) return;
          const { ctx, bbox } = u;
          ctx.save();
          ctx.strokeStyle = '#a3b2b0'; ctx.setLineDash([3, 4]); ctx.lineWidth = devicePixelRatio;
          ctx.fillStyle = '#5f7477'; ctx.font = `${10 * devicePixelRatio}px system-ui`;
          for (const m of markers) {
            const x = u.valToPos(m.t_ms / 1000, 'x', true);
            if (x < bbox.left || x > bbox.left + bbox.width) continue;
            ctx.beginPath(); ctx.moveTo(x, bbox.top); ctx.lineTo(x, bbox.top + bbox.height); ctx.stroke();
            ctx.fillText(m.label, x + 4 * devicePixelRatio, bbox.top + 12 * devicePixelRatio);
          }
          ctx.restore();
        }],
        setSelect: [u => { if (u.select.width > 0) zoomed = true; }]
      }
    };
  }

  function arrays(): uPlot.AlignedData {
    return [data.x, ...data.series.map(s => s.values)] as uPlot.AlignedData;
  }

  function render() {
    if (!host || !hasValues) { plot?.destroy(); plot = null; signature = ''; return; }
    const next = JSON.stringify(data.series.map(s => [s.label, s.color, s.dashed, s.pointsOnly])) + markers.length;
    if (plot && next === signature) {
      plot.setData(arrays(), !zoomed);
      return;
    }
    plot?.destroy();
    signature = next;
    plot = new uPlot(options(host.clientWidth || 600), arrays(), host);
  }

  function resetZoom() {
    zoomed = false;
    plot?.setData(arrays(), true);
  }

  function downloadPng() {
    if (!plot) return;
    const src = plot.ctx.canvas;
    const scale = devicePixelRatio;
    const header = 54 * scale;
    const out = document.createElement('canvas');
    out.width = src.width; out.height = src.height + header;
    const ctx = out.getContext('2d')!;
    ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, out.width, out.height);
    ctx.fillStyle = '#243e45'; ctx.font = `600 ${14 * scale}px system-ui`;
    ctx.fillText(`${title} (${unit})`, 10 * scale, 20 * scale);
    ctx.font = `${11 * scale}px system-ui`;
    let x = 10 * scale;
    for (const s of data.series) {
      ctx.fillStyle = s.color; ctx.fillRect(x, 33 * scale, 12 * scale, 3 * scale);
      ctx.fillStyle = '#4f6467'; ctx.fillText(s.label, x + 16 * scale, 38 * scale);
      x += (ctx.measureText(s.label).width + 34 * scale);
    }
    ctx.drawImage(src, 0, header);
    out.toBlob(blob => {
      if (!blob) return;
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `${filename}.png`;
      link.click();
      URL.revokeObjectURL(link.href);
    });
  }

  $effect(() => { void data; void markers; render(); });

  onMount(() => {
    const observer = new ResizeObserver(() => { if (plot && host.clientWidth) plot.setSize({ width: host.clientWidth, height }); });
    observer.observe(host);
    return () => { observer.disconnect(); plot?.destroy(); plot = null; };
  });
</script>

<article class="chart">
  <div class="heading">
    <h3>{title}</h3>
    <div class="tools">
      <span class="unit">{unit}</span>
      {#if zoomed}<button type="button" onclick={resetZoom}>Restablecer zoom</button>{/if}
      <button type="button" onclick={downloadPng} disabled={!hasValues} title="Descargar este gráfico como PNG">PNG</button>
    </div>
  </div>
  <div class="plot" bind:this={host} class:hidden={!hasValues}></div>
  {#if !hasValues}<div class="empty" style:height={`${height}px`}>Sin lecturas válidas en esta ventana</div>{/if}
  {#if subtitle}<p>{subtitle}</p>{/if}
</article>

<style>
  .chart { background: white; border: 1px solid #dce3e3; border-radius: 10px; padding: 16px 16px 10px; min-width: 0; }
  .heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 4px; }
  h3 { margin: 0; color: #243e45; font-size: 14px; font-weight: 600; }
  .tools { display: flex; align-items: center; gap: 8px; }
  .unit { color: #74878b; font-size: 11px; }
  button { border: 1px solid #dbe4e1; background: #f6f9f8; color: #4f6a6c; border-radius: 4px; font-size: 10px; padding: 3px 7px; cursor: pointer; }
  button:disabled { opacity: .45; cursor: default; }
  .plot.hidden { display: none; }
  .plot :global(.u-legend) { font-size: 11px; color: #5b7175; text-align: left; }
  .plot :global(.u-legend .u-marker) { width: 10px; height: 3px; border-radius: 2px; }
  .plot :global(.u-select) { background: rgba(58, 155, 140, .12); }
  p { margin: 6px 0 0; color: #819195; font-size: 10px; }
  .empty { display: grid; place-items: center; color: #87999c; font-size: 12px; }
</style>

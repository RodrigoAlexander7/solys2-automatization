import type { Point, MinuteRow, Role, ObservatoryConfig } from './api';

export type SeriesSpec = {
  label: string; color: string; dashed?: boolean; pointsOnly?: boolean; values: (number | null)[];
};
export type Aligned = { x: number[]; series: SeriesSpec[] };
export type Row = { id?: number; t: number; address: number; cycle?: number | string; run_id?: string; role: Role;
  label: string; valid: boolean; value: number | null; calc?: number | null; temp?: number | null;
  supply?: number | null; rtt?: number | null; dni?: number | null; ratio?: number | null; qc: string };

const FIXED: Record<number, string> = { 2: '#447ec4', 3: '#d67c2e', 4: '#1d8a76' };
const PALETTE = ['#7966a8', '#c2577a', '#5d8a2f', '#b5892a', '#3b8fa3', '#8a5a44'];

/** Color estable por dirección: 2/3/4 conservan los de siempre; el resto toma la paleta en orden. */
export function colorFor(address: number, addresses: number[] = []): string {
  if (FIXED[address]) return FIXED[address];
  const extra = [...new Set(addresses)].filter(a => !FIXED[a]).sort((a, b) => a - b);
  const index = Math.max(0, extra.indexOf(address));
  return PALETTE[index % PALETTE.length];
}

export const ROLE_NAMES: Record<Role, string> = {
  GHI: 'Global (GHI)', DHI: 'Difusa (DHI)', LWD: 'Onda larga (Ld↓)', SHADE_OFF: 'Sombra no garantizada'
};

export function fromPoint(p: Point): Row {
  return { id: p.id, t: p.t_ms, address: p.address, cycle: p.cycle, run_id: p.run_id, role: p.role, label: p.label,
    valid: p.valid, value: p.valid ? p.measurement : null, calc: p.valid ? p.lw_down_calc : null,
    temp: p.ok ? p.body_temp_c : null, supply: p.ok ? p.supply_v : null, rtt: p.ok ? p.rtt_ms : null,
    dni: p.dni_est_wm2, ratio: p.dhi_ghi_ratio, qc: p.qc };
}

export function fromMinute(m: MinuteRow): Row {
  const valid = m.n_valid > 0;
  return { t: m.minute_ms + 30_000, address: m.address, cycle: `${m.run_id}:${m.minute_ms}`, run_id: m.run_id,
    role: m.role, label: m.label, valid, value: valid ? m.mean_wm2 : null, temp: m.body_temp_c_mean,
    dni: m.dni_est_wm2, ratio: m.dhi_ghi_ratio, qc: m.qc };
}

/** Une sin duplicar por id, ordena por tiempo y descarta lo anterior a la ventana. */
export function mergePoints(previous: Point[], incoming: Point[], since: number): Point[] {
  const byId = new Map(previous.map(p => [p.id, p]));
  for (const p of incoming) byId.set(p.id, p);
  return [...byId.values()].filter(p => p.t_ms >= since).sort((a, b) => a.t_ms - b.t_ms || a.id - b.id);
}

/** Compatibilidad: deduplica por id y conserva los últimos `maximum`. */
export function mergeRows<T extends { id: number }>(previous: T[], incoming: T[], maximum = 3600): T[] {
  const byId = new Map(previous.map(s => [s.id, s]));
  for (const row of incoming) byId.set(row.id, row);
  return [...byId.values()].sort((a, b) => a.id - b.id).slice(-maximum);
}

export type Group = { label: string; color: string; dashed?: boolean; pointsOnly?: boolean;
  filter: (r: Row) => boolean; value: (r: Row) => number | null | undefined };

/**
 * Datos alineados para uPlot. Las lecturas de un ciclo se agrupan en el mismo
 * instante (redondeado al paso); un valor ausente o inválido es null y corta
 * la línea: nunca se interpola ni se completa con ceros.
 */
export function align(rows: Row[], groups: Group[], stepMs: number): Aligned {
  const key = (t: number) => Math.round(t / stepMs) * stepMs;
  const xs = [...new Set(rows.map(r => key(r.t)))].sort((a, b) => a - b);
  const index = new Map(xs.map((x, i) => [x, i]));
  const series = groups.map(g => {
    const values: (number | null)[] = new Array(xs.length).fill(null);
    for (const r of rows) {
      if (!g.filter(r)) continue;
      const v = g.value(r);
      values[index.get(key(r.t))!] = typeof v === 'number' && Number.isFinite(v) ? v : null;
    }
    return { label: g.label, color: g.color, dashed: g.dashed, pointsOnly: g.pointsOnly, values };
  });
  // Huecos de adquisición: si dos instantes consecutivos están separados más de 3 pasos,
  // se inserta un null para que la línea no una lecturas lejanas.
  const x: number[] = [];
  const out = series.map(s => ({ ...s, values: [] as (number | null)[] }));
  xs.forEach((t, i) => {
    if (i && t - xs[i - 1] > 3 * stepMs) {
      x.push((xs[i - 1] + stepMs) / 1000);
      out.forEach(s => s.values.push(null));
    }
    x.push(t / 1000);
    out.forEach((s, k) => s.values.push(series[k].values[i]));
  });
  return { x, series: out };
}

/** Diferencia relativa por ciclo entre dos instrumentos del mismo rol; denominador: media de ambos. */
export function differenceRows(rows: Row[], a: number, b: number): Row[] {
  const cycles = new Map<string, Map<number, Row>>();
  for (const r of rows) {
    const k = `${r.run_id}:${r.cycle}`;
    if (!cycles.has(k)) cycles.set(k, new Map());
    cycles.get(k)!.set(r.address, r);
  }
  const out: Row[] = [];
  for (const pair of cycles.values()) {
    const x = pair.get(a), y = pair.get(b);
    const any = x ?? y;
    if (!any) continue;
    const mean = x?.value != null && y?.value != null ? (x.value + y.value) / 2 : 0;
    out.push({ ...any, address: -1, value: mean > 100 ? 100 * (x!.value! - y!.value!) / mean : null });
  }
  return out;
}

/** Pares del mismo rol presentes en la configuración vigente. */
export function pairsByRole(rows: Row[]): { role: Role; a: number; b: number }[] {
  const latest = new Map<number, Role>();
  for (const r of rows) latest.set(r.address, r.role);
  const byRole = new Map<Role, number[]>();
  for (const [address, role] of latest) {
    if (role === 'SHADE_OFF') continue;
    byRole.set(role, [...(byRole.get(role) ?? []), address].sort((a, b) => a - b));
  }
  const pairs: { role: Role; a: number; b: number }[] = [];
  for (const [role, list] of byRole) for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++)
    pairs.push({ role, a: list[i], b: list[j] });
  return pairs;
}

export function instrumentLabel(config: ObservatoryConfig | null | undefined, address: number): string {
  const label = config?.instruments.find(i => i.address === address)?.label;
  return label ? `${label} (#${address})` : `#${address}`;
}

export function format(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('es', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function localDate(t: number | string, withSeconds = true): string {
  return new Date(t).toLocaleString('es', { dateStyle: 'short', timeStyle: withSeconds ? 'medium' : 'short' });
}

/** `datetime-local` (hora local del navegador) ↔ ISO UTC. */
export function toInputValue(t: number): string {
  const d = new Date(t);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromInputValue(value: string): number | null {
  const t = new Date(value).getTime();
  return Number.isFinite(t) ? t : null;
}

export const QC_TEXT: Record<string, string> = {
  ok: 'QC correcto', suspect: 'QC: sospechoso', bad: 'QC: fuera de límites físicos', na: 'QC no aplica'
};

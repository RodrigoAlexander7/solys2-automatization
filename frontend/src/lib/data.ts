export type Sample = {
  id: number; address: number; cycle: number; time_utc: string;
  sensor_type: 'pyranometer' | 'pyrgeometer'; valid: boolean; ok: boolean;
  measurement: number | null; shortwave_wm2: number | null;
  net_longwave_wm2: number | null; lw_down: number | null; lw_down_calc: number | null;
  lw_check_delta: number | null; body_temp_c: number | null; supply_v: number | null;
  rtt_ms: number | null; status: number | null; error: string; error_code: string | null;
  stale?: boolean; age_s?: number;
};
export type Run = {
  id: string; started_utc: string; ended_utc: string | null; state: string;
  metadata: { simulated: boolean; config: { label: string; interval_s: number; addresses: number[] } };
  status: { cycle?: number; cycle_ms?: number; port_errors?: number; overruns?: number };
};
export type Status = {
  run: Run; latest: Sample[]; comparison: { difference_wm2: number | null; difference_pct: number | null };
  heartbeat_age_s: number; acquisition_stale: boolean; stale_s: number;
};
export type Page = { items: Sample[]; next_cursor: number; has_more: boolean; history_truncated: boolean };
export type ApiError = { code: string; message: string; trace_id?: string };
export type Result<T> = { ok: true; value: T } | { ok: false; error: ApiError };
export type Point = { time: number; value: number | null };
export type Series = { label: string; color: string; points: Point[]; dashed?: boolean };

export async function get<T>(path: string): Promise<Result<T>> {
  try {
    const response = await fetch(path, { signal: AbortSignal.timeout(10000) });
    const data = await response.json();
    if (!response.ok) return { ok: false, error: {
      code: data.error?.code ?? 'http_error', message: data.error?.message ?? `HTTP ${response.status}`,
      trace_id: data.trace_id ?? response.headers.get('X-Trace-ID')
    } };
    return { ok: true, value: data as T };
  } catch (error) {
    return { ok: false, error: { code: 'network_error', message: 'Sin conexión con la API. Reintentando…' } };
  }
}

export function mergeRows(previous: Sample[], incoming: Sample[], maximum = 3600): Sample[] {
  const byId = new Map(previous.map(s => [s.id, s]));
  for (const row of incoming) byId.set(row.id, row);
  return [...byId.values()].sort((a, b) => a.id - b.id).slice(-maximum);
}

export function makeSeries(rows: Sample[], addresses: number[], key: keyof Sample,
                           interval = 1, labelPrefix = '#'): Series[] {
  const colors: Record<number, string> = { 2: '#447ec4', 3: '#d67c2e', 4: '#1d8a76' };
  return addresses.map(address => {
    const points: Point[] = [];
    for (const row of rows.filter(s => s.address === address)) {
      const time = Date.parse(row.time_utc);
      const previous = points.at(-1);
      if (previous && (time - previous.time > interval * 3000 || time < previous.time))
        points.push({ time, value: null });
      const value = row[key];
      points.push({ time, value: row.valid && typeof value === 'number' && Number.isFinite(value) ? value : null });
    }
    return { label: `${labelPrefix}${address}`, color: colors[address] ?? '#7966a8', points };
  });
}

export function differenceSeries(rows: Sample[]): Series[] {
  const cycles = new Map<number, Map<number, Sample>>();
  for (const row of rows) {
    if (!cycles.has(row.cycle)) cycles.set(row.cycle, new Map());
    cycles.get(row.cycle)!.set(row.address, row);
  }
  return [{ label: '#3 − #4', color: '#7966a8', points: [...cycles.values()].map(pair => {
    const a = pair.get(3), b = pair.get(4);
    const mean = a?.valid && b?.valid && a.measurement !== null && b.measurement !== null
      ? (a.measurement + b.measurement) / 2 : 0;
    return { time: Date.parse((a ?? b ?? [...pair.values()][0]).time_utc),
      value: mean > 100 ? 100 * (a!.measurement! - b!.measurement!) / mean : null };
  }) }];
}

export function format(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('es', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export type ApiError = { code: string; message: string; trace_id?: string | null };
export type Result<T> = { ok: true; value: T } | { ok: false; error: ApiError };
export type Role = 'GHI' | 'DHI' | 'LWD' | 'SHADE_OFF';
export type QcCode = 'ok' | 'fail' | 'na';
export type QcSummary = 'ok' | 'suspect' | 'bad' | 'na';

export type QcResult = {
  ppl: QcCode; erl: QcCode; ratio: QcCode; shading: QcCode; comparison: QcCode;
  summary: QcSummary; notes: string[];
};

/** Muestra completa (estado y detalle). */
export type Sample = {
  id: number; run_id: string; address: number; cycle: number; time_utc: string; t_ms: number;
  sensor_type: 'pyranometer' | 'pyrgeometer'; valid: boolean; ok: boolean;
  measurement: number | null; shortwave_wm2: number | null; net_longwave_wm2: number | null;
  lw_down: number | null; lw_down_calc: number | null; lw_check_delta: number | null;
  body_temp_c: number | null; supply_v: number | null; rtt_ms: number | null;
  status: number | null; mode: number | null; error: string; error_code: string | null;
  role: Role; role_label: string; label: string; config_version: number | null;
  solar_zenith_deg: number | null; kt: number | null; dhi_ghi_ratio: number | null; dni_est_wm2: number | null;
  qc: QcResult; stale?: boolean; age_s?: number;
};

/** Muestra reducida para gráficos (series y SSE). */
export type Point = {
  id: number; run_id: string; cycle: number; address: number; t_ms: number; sensor_type: string;
  ok: boolean; valid: boolean; measurement: number | null; lw_down_calc: number | null;
  body_temp_c: number | null; supply_v: number | null; rtt_ms: number | null; status: number | null;
  role: Role; label: string; dni_est_wm2: number | null; dhi_ghi_ratio: number | null;
  solar_zenith_deg: number | null; qc: QcSummary; qc_failed: string[];
};

export type MinuteRow = {
  run_id: string; address: number; minute_ms: number; minute_utc: string; sensor_type: string;
  n_samples: number; n_valid: number; mean_wm2: number | null; min_wm2: number | null; max_wm2: number | null;
  std_wm2: number | null; complete: boolean; role: Role; label: string; dni_est_wm2: number | null;
  dhi_ghi_ratio: number | null; solar_zenith_deg: number | null; qc: QcSummary; qc_failed: string[];
  body_temp_c_mean: number | null;
};

export type Site = { name: string; latitude: number; longitude: number; altitude_m: number };
export type InstrumentConfig = { address: number; shaded: boolean; label: string };
export type QcConfig = { comparison_pct: number; shading_kt_min: number; shading_ratio_max: number };
export type ObservatoryConfig = {
  site: Site | null; solys2_in_use: boolean; instruments: InstrumentConfig[]; qc: QcConfig;
};
export type ConfigVersion = {
  id: number; valid_from_ms: number; valid_from: string; created: string; author: string; note: string;
  config: ObservatoryConfig;
};

export type Run = {
  id: string; started_utc: string; ended_utc: string | null; state: string;
  metadata: { simulated: boolean; bus?: string; config: { label: string; interval_s: number; addresses: number[]; pyrgeometers?: number[] } };
  status: { cycle?: number; cycle_ms?: number; port_errors?: number; overruns?: number;
            archive?: { enabled: boolean; retention_days: number; archived_days: number; pruned_days: number; last_error: string } };
};

export type ClockCheck = {
  t_ms: number; server: string; ok: boolean; offset_s: number | null; delay_s: number | null; error: string;
  within_limit: boolean; max_offset_s: number;
};
export type Comparison = { role: Role; a: number; b: number; difference_wm2: number | null; difference_pct: number | null };
export type Alert = { level: 'warning' | 'error'; text: string };

export type Live = {
  run: Run | null; latest: Sample[]; comparisons: Comparison[]; alerts: Alert[];
  config: ObservatoryConfig; config_version?: number | null;
  clock: { enabled: boolean; check: ClockCheck | null; age_s: number | null; warning: string | null };
  heartbeat_age_s?: number; stale_s?: number; acquisition_stale?: boolean;
};

export type LogbookEntry = {
  id: number; t_ms: number; time_utc: string; created: string; author: string; category: string;
  category_label: string; addresses: number[]; text: string; source: string; voided: boolean;
  voided_at: string | null; voided_by: string | null; void_reason: string | null;
};

export type TimelineItem = {
  t_ms: number; time_utc: string; source: 'adquisicion' | 'bitacora' | 'configuracion'; kind: string;
  severity: 'info' | 'warning' | 'error'; address: number | null; title: string;
  detail?: Record<string, unknown>; entry?: LogbookEntry; version?: ConfigVersion; run_id?: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  try {
    const response = await fetch(path, { ...init, signal: init?.signal ?? AbortSignal.timeout(20000) });
    const data = await response.json();
    if (!response.ok) return { ok: false, error: {
      code: data.error?.code ?? 'http_error', message: data.error?.message ?? `HTTP ${response.status}`,
      trace_id: data.trace_id ?? response.headers.get('X-Trace-ID')
    } };
    return { ok: true, value: data as T };
  } catch {
    return { ok: false, error: { code: 'network_error', message: 'Sin conexión con la API. Reintentando…' } };
  }
}

export const get = <T>(path: string, signal?: AbortSignal) => request<T>(path, signal ? { signal } : undefined);

export const post = <T>(path: string, body: unknown) => request<T>(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
});

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

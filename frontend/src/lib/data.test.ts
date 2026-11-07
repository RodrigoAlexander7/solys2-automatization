import { describe, expect, test } from 'bun:test';
import { align, colorFor, differenceRows, format, fromMinute, fromPoint, mergePoints, mergeRows, pairsByRole,
         toInputValue, fromInputValue, type Row } from './data';
import { render, titleOf } from './docs-render';
import type { MinuteRow, Point } from './api';

const point = (id: number, value: number | null = 100, address = 3, role: Point['role'] = 'GHI', t = 1000 * id): Point => ({
  id, run_id: 'r', cycle: id, address, t_ms: t, sensor_type: address === 2 ? 'pyrgeometer' : 'pyranometer', ok: true,
  valid: value !== null, measurement: value, lw_down_calc: null, body_temp_c: 25, supply_v: 12, rtt_ms: 20, status: 0,
  role, label: `#${address}`, dni_est_wm2: null, dhi_ghi_ratio: null, solar_zenith_deg: 40, qc: 'ok', qc_failed: []
});
const row = (cycle: number, address: number, value: number | null, role: Row['role'] = 'GHI'): Row =>
  ({ t: cycle * 1000, address, cycle, run_id: 'r', role, label: `#${address}`, valid: value !== null, value, qc: 'ok' });

describe('presentación científica de datos', () => {
  test('deduplica repeticiones y acota la memoria', () => {
    expect(mergeRows([{ id: 1 }, { id: 2 }], [{ id: 2 }, { id: 3 }], 2).map(s => s.id)).toEqual([2, 3]);
    expect(mergePoints([point(1), point(2)], [point(2), point(3)], 2000).map(p => p.id)).toEqual([2, 3]);
  });
  test('cero no es ausente', () => {
    expect(format(0)).toBe('0,0');
    expect(format(null)).toBe('—');
    expect(fromPoint(point(1, 0)).value).toBe(0);
    expect(fromPoint(point(1, null)).value).toBeNull();
  });
  test('una lectura inválida no aporta valor aunque traiga número', () => {
    const p = { ...point(1, 812), valid: false };
    expect(fromPoint(p).value).toBeNull();
  });
  test('sin porcentaje con baja irradiancia o sin pareja', () => {
    expect(differenceRows([row(1, 3, 0), row(1, 4, 0)], 3, 4)[0].value).toBeNull();
    expect(differenceRows([row(1, 3, 500)], 3, 4)[0].value).toBeNull();
  });
  test('diferencia relativa con denominador simétrico explícito', () => {
    expect(differenceRows([row(1, 3, 300), row(1, 4, 200)], 3, 4)[0].value).toBe(40);
  });
  test('no compara ciclos distintos', () => {
    const out = differenceRows([row(1, 3, 300), row(2, 4, 200)], 3, 4);
    expect(out.every(r => r.value === null)).toBe(true);
  });
  test('huecos e inválidos cortan las líneas', () => {
    const rows = [row(1, 3, 100), row(2, 3, null), row(20, 3, 120)];
    const aligned = align(rows, [{ label: '#3', color: '#000', filter: () => true, value: r => r.value }], 1000);
    expect(aligned.series[0].values.filter(v => v === null).length).toBe(2);
    expect(aligned.x.length).toBe(4);
  });
  test('lecturas secuenciales del mismo ciclo comparten instante', () => {
    const a = { ...row(5, 3, 100), t: 5000 + 20 }, b = { ...row(5, 4, 101), t: 5000 + 70 };
    const aligned = align([a, b], [3, 4].map(n => ({ label: `#${n}`, color: '#000', filter: (r: Row) => r.address === n, value: (r: Row) => r.value })), 1000);
    expect(aligned.x).toEqual([5]);
    expect(aligned.series.map(s => s.values[0])).toEqual([100, 101]);
  });
  test('solo se comparan instrumentos del mismo rol', () => {
    const pairs = pairsByRole([row(1, 3, 1, 'GHI'), row(1, 4, 1, 'DHI'), row(1, 5, 1, 'GHI'), row(1, 6, 1, 'SHADE_OFF')]);
    expect(pairs).toEqual([{ role: 'GHI', a: 3, b: 5 }]);
  });
  test('colores estables por dirección, con paleta para equipos nuevos', () => {
    expect(colorFor(3)).toBe('#d67c2e');
    expect(colorFor(7, [2, 3, 4, 7, 9])).not.toBe(colorFor(9, [2, 3, 4, 7, 9]));
  });
  test('promedios de minuto: se ubican en el centro del minuto y sin válidas no hay valor', () => {
    const m = { minute_ms: 60_000, address: 3, n_valid: 0, mean_wm2: null, role: 'GHI', label: '#3', run_id: 'r', qc: 'na' } as unknown as MinuteRow;
    expect(fromMinute(m).t).toBe(90_000);
    expect(fromMinute(m).value).toBeNull();
  });
  test('entrada datetime-local ida y vuelta', () => {
    const t = new Date(2026, 5, 21, 10, 30).getTime();
    expect(fromInputValue(toInputValue(t))).toBe(t);
    expect(fromInputValue('')).toBeNull();
  });
});

describe('documentación', () => {
  test('enlaces entre documentos y externos', () => {
    const html = render('# Título\n\nVer [calidad](03-calidad-de-datos.md#seccion) y [BSRN](https://bsrn.awi.de).');
    expect(html).toContain('href="#03-calidad-de-datos"');
    expect(html).toContain('target="_blank"');
    expect(titleOf('# Hola\n\ntexto', 'x')).toBe('Hola');
  });
});

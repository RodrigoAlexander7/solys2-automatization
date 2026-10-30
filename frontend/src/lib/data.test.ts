import { describe, expect, test } from 'bun:test';
import { mergeRows, differenceSeries, makeSeries, format, type Sample } from './data';

const row = (id: number, value: number | null = 100, address = 3): Sample => ({
  id, address, cycle: 1, time_utc: new Date(1000 * id).toISOString(), sensor_type: 'pyranometer',
  valid: value !== null, ok: true, measurement: value, shortwave_wm2: value,
  net_longwave_wm2: null, lw_down: null, lw_down_calc: null, lw_check_delta: null,
  body_temp_c: 25, supply_v: 12, rtt_ms: 20, status: 0, error: '', error_code: null
});

describe('scientific data presentation', () => {
  test('deduplicates replay and bounds memory', () => {
    expect(mergeRows([row(1), row(2)], [row(2), row(3)], 2).map(s => s.id)).toEqual([2, 3]);
  });
  test('zero is not missing', () => {
    expect(format(0)).toBe('0,0');
    expect(format(null)).toBe('—');
  });
  test('low light and absent pair have no relative difference', () => {
    expect(differenceSeries([row(1, 0), row(2, 0, 4)])[0].points[0].value).toBeNull();
    expect(differenceSeries([row(1)])[0].points[0].value).toBeNull();
  });
  test('relative difference has explicit symmetric denominator', () => {
    expect(differenceSeries([row(1, 300), row(2, 200, 4)])[0].points[0].value).toBe(40);
  });
  test('gaps and invalid readings break lines', () => {
    const points = makeSeries([row(1), row(2, null), row(20)], [3], 'measurement')[0].points;
    expect(points.filter(p => p.value === null).length).toBe(2);
  });
});

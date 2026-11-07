"""Escenarios de falla de una estación radiométrica (R19–R21, R27; C11, C14).

Cada escenario simula días completos con el sol del sitio de referencia y una
falla inyectada, y comprueba que el control de calidad o los eventos la señalen
sin modificar los valores medidos. Las fallas son simuladas: estos tests
validan la lógica del programa, no el comportamiento real de los instrumentos.
"""
import tempfile
import unittest
from pathlib import Path

from smp22x.result import require
from smp22x.simulator import Faults
from smp22x.storage import Reader
from scenario import annotated, config, simulate, version

DAY_MS = 86_400_000


def daytime(rows, max_zenith=75):
    return [r for r in rows if r["solar_zenith_deg"] is not None and r["solar_zenith_deg"] < max_zenith]


def night(rows):
    return [r for r in rows if r["solar_zenith_deg"] is not None and r["solar_zenith_deg"] > 100]


def share(rows, test, code="fail"):
    return sum(r["qc"][test] == code for r in rows) / max(1, len(rows))


class Scenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "escenario.sqlite3"

    def run_day(self, cfg, days=1, step_s=120, **sim):
        info = simulate(self.db, hours=24 * days, step_s=step_s, **sim)
        rows = annotated(self.db, [version(cfg)], info["start_ms"], info["end_ms"])
        return rows, info

    def of(self, rows, address):
        return [r for r in rows if r["address"] == address]


class HealthyStationTests(Scenario):
    def test_clear_day_has_no_failures_in_daylight(self):
        rows, _ = self.run_day(config(solys2=True, shaded=(4,)), shaded=(4,))
        day = daytime(rows, 85)
        self.assertTrue(day)
        self.assertFalse([r for r in day if r["qc"]["summary"] in ("bad", "suspect")])
        self.assertFalse([r for r in rows if r["qc"]["summary"] == "bad"])
        dhi = daytime(self.of(rows, 4))
        self.assertTrue(all(r["role"] == "DHI" and r["qc"]["shading"] != "fail" for r in dhi))
        self.assertGreater(share(dhi, "shading", "ok"), 0.5)  # aplica con sol alto y cielo claro
        self.assertTrue(all(r["dni_est_wm2"] and 300 < r["dni_est_wm2"] < 1100 for r in dhi))

    def test_longwave_is_checked_without_site(self):
        rows, _ = self.run_day(config(site=False))
        lwd = self.of(rows, 2)
        self.assertTrue(all(r["qc"]["ppl"] == "ok" for r in lwd))
        self.assertTrue(all(r["qc"]["ppl"] == "na" for r in self.of(rows, 3)))


class ShadingFaultTests(Scenario):
    def test_ball_not_covering_the_sun_is_detected(self):
        rows, _ = self.run_day(config(solys2=True, shaded=(4,)), shaded=(4,), faults={4: Faults(shade_miss=1.0)})
        dhi = daytime(self.of(rows, 4))
        self.assertGreater(share(dhi, "shading"), 0.8)
        self.assertLess(share(dhi, "ratio"), 0.05)  # el cociente BSRN por sí solo no lo vería

    def test_tracker_stopped_but_declared_in_use(self):
        rows, _ = self.run_day(config(solys2=True, shaded=(4,)), shaded=(4,), tracker_active=False)
        self.assertGreater(share(daytime(self.of(rows, 4)), "shading"), 0.8)

    def test_tracker_declared_inactive(self):
        rows, _ = self.run_day(config(solys2=False, shaded=(4,)), shaded=(4,), tracker_active=False)
        shaded = self.of(rows, 4)
        self.assertTrue(all(r["role"] == "SHADE_OFF" for r in shaded))
        self.assertTrue(all(r["qc"]["shading"] == "na" and r["dni_est_wm2"] is None for r in shaded))
        self.assertTrue(all(r["measurement"] is not None for r in shaded if r["valid"]))  # se sigue registrando

    def test_configuration_change_mid_day_keeps_earlier_interpretation(self):
        info = simulate(self.db, hours=24, step_s=300, shaded=(4,))
        noon = info["start_ms"] + 12 * 3_600_000
        versions = [version(config(solys2=False, shaded=(4,)), 0, 1), version(config(solys2=True, shaded=(4,)), noon, 2)]
        rows = annotated(self.db, versions, info["start_ms"], info["end_ms"])
        shaded = self.of(rows, 4)
        self.assertTrue(all(r["role"] == "SHADE_OFF" for r in shaded if r["t_ms"] < noon))
        self.assertTrue(all(r["role"] == "DHI" for r in shaded if r["t_ms"] >= noon))


class InstrumentFaultTests(Scenario):
    def test_night_offset_breaks_physical_limit(self):
        rows, _ = self.run_day(config(), faults={3: Faults(offset_wm2=-8.0)})
        self.assertGreater(share(night(self.of(rows, 3)), "ppl"), 0.95)
        self.assertEqual(share(night(self.of(rows, 4)), "ppl"), 0)
        sample = night(self.of(rows, 3))[0]
        self.assertLess(sample["measurement"], -6)  # el valor se conserva tal cual

    def test_dirty_dome_drift_detected_by_comparison(self):
        rows, info = self.run_day(config(), days=4, step_s=600, faults={4: Faults(gain_drift_per_day=-0.025)})
        ghi = daytime([r for r in rows if r["role"] == "GHI"])
        first = [r for r in ghi if r["t_ms"] < info["start_ms"] + DAY_MS]
        last = [r for r in ghi if r["t_ms"] >= info["start_ms"] + 3 * DAY_MS]
        self.assertLess(share(first, "comparison"), 0.1)
        self.assertGreater(share(last, "comparison"), 0.9)

    def test_misleveling_produces_morning_afternoon_asymmetry(self):
        rows, _ = self.run_day(config(), step_s=300, faults={4: Faults(tilt_deg=5.0, tilt_azimuth_deg=90.0)})
        by_cycle = {}
        for r in daytime(rows, 70):
            by_cycle.setdefault(r["cycle"], {})[r["address"]] = r
        pairs = [(c[3], c[4]) for c in by_cycle.values() if 3 in c and 4 in c]
        morning = [b["measurement"] - a["measurement"] for a, b in pairs if a["t_ms"] < pairs[len(pairs) // 2][0]["t_ms"]]
        afternoon = [b["measurement"] - a["measurement"] for a, b in pairs if a["t_ms"] > pairs[len(pairs) // 2][0]["t_ms"]]
        self.assertGreater(sum(morning) / len(morning), 10)    # inclinado al este: lee más a la mañana
        self.assertLess(sum(afternoon) / len(afternoon), -10)
        self.assertGreater(share([a for a, _ in pairs], "comparison"), 0.2)

    def test_desiccant_warning_is_an_event_not_invalid_data(self):
        rows, info = self.run_day(config(), step_s=600, faults={3: Faults(status_bits=1 << 11)})
        self.assertTrue(all(r["valid"] for r in self.of(rows, 3)))
        events = require(Reader(self.db).events(info["start_ms"], info["end_ms"] + 1))
        desiccant = [e for e in events if e["address"] == 3 and "DESICCANT_WARNING" in e["detail"].get("set", [])]
        self.assertEqual(len(desiccant), 1)
        self.assertEqual(desiccant[0]["severity"], "warning")

    def test_void_data_bursts_invalidate_and_are_rate_limited(self):
        info = simulate(self.db, hours=6, step_s=5, faults={3: Faults(void_every_s=60)})
        rows = annotated(self.db, [version(config())], info["start_ms"], info["end_ms"])
        invalid = [r for r in self.of(rows, 3) if not r["valid"]]
        self.assertGreater(len(invalid), 300)
        self.assertTrue(all(r["qc"]["summary"] == "na" for r in invalid))
        events = [e for e in require(Reader(self.db).events(info["start_ms"], info["end_ms"] + 1, 100_000))
                  if e["address"] == 3 and e["kind"].startswith("flags")]
        self.assertTrue(events)
        self.assertLessEqual(len(events), 6 * 3600 / 10 + 2)  # como máximo uno cada 10 s

    def test_silent_sensor_gap_is_recorded_not_filled(self):
        rows, info = self.run_day(config(), step_s=30, faults={3: Faults(silent=(3600, 7200))})
        failed = [r for r in self.of(rows, 3) if not r["ok"]]
        self.assertEqual(len(failed), 120)
        self.assertTrue(all(r["measurement"] is None for r in failed))
        kinds = [e["kind"] for e in require(Reader(self.db).events(info["start_ms"], info["end_ms"] + 1))
                 if e["address"] == 3]
        self.assertEqual(kinds.count("sensor_silent"), 1)
        self.assertEqual(kinds.count("sensor_recovered"), 1)
        minutes = annotated(self.db, [version(config())], info["start_ms"], info["end_ms"], minutes=True)
        gap = [m for m in minutes if m["address"] == 3 and info["start_ms"] + 3_600_000 <= m["minute_ms"] < info["start_ms"] + 7_200_000]
        self.assertTrue(gap and all(m["n_valid"] == 0 and m["mean_wm2"] is None for m in gap))


class MinuteQcTests(Scenario):
    def test_minute_means_are_qc_checked_like_samples(self):
        info = simulate(self.db, hours=24, step_s=60, shaded=(4,), faults={4: Faults(shade_miss=1.0)})
        minutes = annotated(self.db, [version(config(solys2=True, shaded=(4,)))], info["start_ms"], info["end_ms"], minutes=True)
        dhi = daytime([m for m in minutes if m["address"] == 4])
        self.assertGreater(share(dhi, "shading"), 0.8)
        self.assertTrue(all(m["role"] == "DHI" for m in dhi))


if __name__ == "__main__":
    unittest.main()

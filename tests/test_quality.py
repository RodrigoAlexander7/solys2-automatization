"""Reglas metrológicas (R18–R21; C14): posición solar, límites BSRN, pruebas del proyecto y propiedades."""
import math
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
from hypothesis import given, settings, strategies as st

from smp22x import qc
from smp22x.analysis import ConfigTimeline, annotate_samples, role_of
from smp22x.sensor import scale_sensor_value, to_s16
from smp22x.solar import SOLAR_CONSTANT, solar_position

FAST = settings(max_examples=150, deadline=None)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class SolarPositionTests(unittest.TestCase):
    def test_nrel_spa_reference_vector(self):
        """Reda y Andreas (2004), tabla A5.1: 2003-10-17 12:30:30 −7 h, Golden (CO)."""
        t = datetime(2003, 10, 17, 12, 30, 30, tzinfo=timezone(timedelta(hours=-7)))
        p = solar_position(ms(t), 39.742476, -105.1786, 1830.14, pressure_hpa=820, temp_c=11)
        self.assertAlmostEqual(float(p.zenith[0]), 50.11162, delta=0.02)
        self.assertAlmostEqual(float(p.azimuth[0]), 194.34024, delta=0.02)
        self.assertAlmostEqual(float(p.distance_au[0]), 0.9965422974, delta=1e-4)

    def test_equinox_noon_at_equator_is_near_zenith(self):
        t = datetime(2026, 3, 20, 12, 7, tzinfo=timezone.utc)  # mediodía solar ≈ 12:07 UTC en lon 0
        p = solar_position(ms(t), 0.0, 0.0)
        self.assertLess(float(p.zenith[0]), 1.0)

    def test_morning_afternoon_symmetry(self):
        approx = ms(datetime(2026, 6, 21, 19, 0, tzinfo=timezone.utc))
        search = np.arange(approx - 3_600_000, approx + 3_600_000, 10_000)
        noon = int(search[np.argmin(solar_position(search, 39.74, -105.18).zenith)])
        for hours in (1, 2, 3, 4):
            d = hours * 3_600_000
            z = solar_position([noon - d, noon + d], 39.74, -105.18).zenith
            self.assertAlmostEqual(z[0], z[1], delta=0.1, msg=f"±{hours} h")

    def test_night_has_zero_mu0_and_extraterrestrial_follows_distance(self):
        p = solar_position(ms(datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)), 39.74, -105.18)
        self.assertGreater(float(p.zenith[0]), 90)
        self.assertEqual(float(p.mu0[0]), 0.0)
        jan = solar_position(ms(datetime(2026, 1, 3, tzinfo=timezone.utc)), 0, 0).extraterrestrial[0]
        jul = solar_position(ms(datetime(2026, 7, 4, tzinfo=timezone.utc)), 0, 0).extraterrestrial[0]
        self.assertGreater(jan, SOLAR_CONSTANT * 1.03)  # perihelio
        self.assertLess(jul, SOLAR_CONSTANT * 0.97)     # afelio


class BsrnLimitTests(unittest.TestCase):
    """Cada límite en su borde: el valor en el límite pasa y un poco más allá falla."""

    def check(self, role, value, sa=1361.0, mu0=1.0, expected=("ok", "ok")):
        ppl, erl = qc.range_test(role, [value], [sa], [mu0])
        self.assertEqual((ppl[0], erl[0]), expected, f"{role} {value}")

    def test_ghi_limits(self):
        sa, mu0 = 1361.0, 0.5
        ppl_hi = 1.5 * sa * mu0 ** 1.2 + 100
        erl_hi = 1.2 * sa * mu0 ** 1.2 + 50
        self.check("GHI", ppl_hi, sa, mu0, ("ok", "fail"))
        self.check("GHI", ppl_hi + 0.01, sa, mu0, ("fail", "fail"))
        self.check("GHI", erl_hi, sa, mu0, ("ok", "ok"))
        self.check("GHI", erl_hi + 0.01, sa, mu0, ("ok", "fail"))
        self.check("GHI", -2.0, expected=("ok", "ok"))
        self.check("GHI", -2.01, expected=("ok", "fail"))
        self.check("GHI", -4.01, expected=("fail", "fail"))

    def test_dhi_limits(self):
        sa, mu0 = 1361.0, 0.8
        self.check("DHI", 0.95 * sa * mu0 ** 1.2 + 50 + 0.01, sa, mu0, ("fail", "fail"))
        self.check("DHI", 0.75 * sa * mu0 ** 1.2 + 30 + 0.01, sa, mu0, ("ok", "fail"))

    def test_longwave_limits_do_not_need_the_sun(self):
        for value, expected in ((40, ("ok", "fail")), (39.9, ("fail", "fail")), (60, ("ok", "ok")),
                                (500, ("ok", "ok")), (500.1, ("ok", "fail")), (700.1, ("fail", "fail"))):
            ppl, erl = qc.range_test("LWD", [value], solar_known=False)
            self.assertEqual((ppl[0], erl[0]), expected, value)

    def test_example_low_sun(self):
        """Sol a 10° de elevación: más de ~350 W/m² de global es físicamente imposible."""
        mu0 = math.cos(math.radians(80))
        limit = 1.5 * 1361 * mu0 ** 1.2 + 100
        self.assertTrue(340 < limit < 360)

    def test_no_site_means_not_applicable(self):
        ppl, erl = qc.range_test("GHI", [5000.0], solar_known=False)
        self.assertEqual((ppl[0], erl[0]), ("na", "na"))

    def test_missing_value_is_not_applicable(self):
        ppl, _ = qc.range_test("GHI", [np.nan], [1361], [1.0])
        self.assertEqual(ppl[0], "na")


class ComparisonTestsBsrnAndProject(unittest.TestCase):
    def test_diffuse_ratio_boundaries(self):
        cases = [  # (ghi, dhi, zenith, esperado)
            (100, 104.9, 60, "ok"), (100, 105, 60, "fail"), (100, 109, 75, "ok"), (100, 110, 75, "fail"),
            (100, 200, 93, "na"), (50, 100, 30, "na"), (50.1, 60, 30, "fail"), (np.nan, 10, 30, "na"),
        ]
        for g, d, z, expected in cases:
            self.assertEqual(qc.ratio_test([g], [d], [z])[0], expected, (g, d, z))

    def test_shading_detects_ball_not_covering_on_clear_sky(self):
        t = qc.Thresholds()
        sa, z = 1361.0, 30.0
        mu0 = math.cos(math.radians(z))
        ghi = 0.75 * sa * mu0  # kt = 0,75
        self.assertEqual(qc.shading_test([ghi], [0.95 * ghi], [z], [sa], [mu0], t)[0], "fail")
        self.assertEqual(qc.shading_test([ghi], [0.15 * ghi], [z], [sa], [mu0], t)[0], "ok")
        # Cielo cubierto (kt bajo): difusa ≈ global es normal, la prueba no aplica.
        self.assertEqual(qc.shading_test([0.3 * sa * mu0], [0.3 * sa * mu0], [z], [sa], [mu0], t)[0], "na")
        # El cociente BSRN no lo detecta: por eso existe la prueba de sombreado.
        self.assertEqual(qc.ratio_test([ghi], [0.95 * ghi], [z])[0], "ok")

    def test_comparison_threshold_and_low_light(self):
        t = qc.Thresholds(comparison_pct=5)
        self.assertEqual(qc.comparison_test([1000], [951], t)[0], "fail")
        self.assertEqual(qc.comparison_test([1000], [960], t)[0], "ok")
        self.assertEqual(qc.comparison_test([90], [10], t)[0], "na")

    def test_estimated_dni_only_below_80_degrees(self):
        dni = qc.estimated_dni([700, 300], [100, 50], [60, 85])
        self.assertAlmostEqual(dni[0], 600 / math.cos(math.radians(60)))
        self.assertTrue(np.isnan(dni[1]))

    def test_summary(self):
        self.assertEqual(qc.summary({"ppl": "fail", "erl": "fail"}), "bad")
        self.assertEqual(qc.summary({"ppl": "ok", "erl": "fail"}), "suspect")
        self.assertEqual(qc.summary({"ppl": "ok", "ratio": "na"}), "ok")
        self.assertEqual(qc.summary({"ppl": "na", "erl": "na"}), "na")


class RoleTests(unittest.TestCase):
    def test_role_table(self):
        self.assertEqual(role_of("pyrgeometer", True, True), "LWD")
        self.assertEqual(role_of("pyranometer", False, True), "GHI")
        self.assertEqual(role_of("pyranometer", True, True), "DHI")
        self.assertEqual(role_of("pyranometer", True, False), "SHADE_OFF")


def record(address, value, t_ms, cycle=0, valid=True, sensor_type="pyranometer"):
    return {"address": address, "sensor_type": sensor_type, "valid": valid, "measurement": value,
            "t_ms": t_ms, "cycle": cycle, "run_id": "r"}


SITE = {"latitude": 39.742476, "longitude": -105.1786, "altitude_m": 1830}


def timeline(solys2=True, shaded=(4,), site=True):
    return ConfigTimeline.build([{"id": 1, "valid_from_ms": 0, "config": {
        "site": SITE if site else None, "solys2_in_use": solys2,
        "instruments": [{"address": a, "shaded": a in shaded} for a in (2, 3, 4)]}}], (2, 3, 4))


class AnnotationPropertyTests(unittest.TestCase):
    @FAST
    @given(st.lists(st.one_of(st.none(), st.floats(-50, 2000, allow_nan=False)), min_size=1, max_size=30),
           st.integers(1_700_000_000_000, 1_900_000_000_000))
    def test_qc_never_modifies_measured_values(self, values, t0):
        rows = [record(3 + (i % 2), v, t0 + 1000 * (i // 2), cycle=i // 2, valid=v is not None)
                for i, v in enumerate(values)]
        before = [(r["measurement"], r["valid"]) for r in rows]
        annotate_samples(rows, timeline())
        self.assertEqual(before, [(r["measurement"], r["valid"]) for r in rows])
        for r in rows:
            self.assertIn(r["qc"]["summary"], {"ok", "suspect", "bad", "na"})
            if not r["valid"]:
                self.assertTrue(all(r["qc"][k] == "na" for k in ("ppl", "erl")))

    @FAST
    @given(st.floats(-1000, 3000, allow_nan=False), st.floats(0.01, 1.0), st.floats(1300, 1420))
    def test_ppl_is_looser_than_erl(self, value, mu0, sa):
        ppl, erl = qc.range_test("GHI", [value], [sa], [mu0])
        if ppl[0] == "fail":
            self.assertEqual(erl[0], "fail")

    def test_solys2_inactive_disables_diffuse_tests(self):
        t = ms(datetime(2026, 6, 21, 19, 0, tzinfo=timezone.utc))
        rows = [record(3, 900, t), record(4, 880, t)]
        annotate_samples(rows, timeline(solys2=False))
        shaded = rows[1]
        self.assertEqual(shaded["role"], "SHADE_OFF")
        self.assertEqual((shaded["qc"]["ratio"], shaded["qc"]["shading"]), ("na", "na"))
        self.assertIsNone(shaded["dni_est_wm2"])
        self.assertIn("SOLYS2 inactivo", shaded["qc"]["notes"][0])
        self.assertEqual(rows[0]["qc"]["comparison"], "na")  # no se compara global con sombra no garantizada

    def test_shading_failure_marks_both_rows_when_solys2_in_use(self):
        t = ms(datetime(2026, 6, 21, 19, 0, tzinfo=timezone.utc))
        rows = [record(3, 900, t), record(4, 880, t)]
        annotate_samples(rows, timeline(solys2=True))
        self.assertEqual(rows[1]["role"], "DHI")
        self.assertEqual(rows[1]["qc"]["shading"], "fail")
        self.assertEqual(rows[0]["qc"]["shading"], "fail")
        self.assertEqual(rows[1]["qc"]["summary"], "suspect")

    def test_ratio_value_only_with_enough_global(self):
        t = ms(datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc))  # noche en el sitio
        rows = [record(3, 1.0, t), record(4, 0.5, t)]
        annotate_samples(rows, timeline())
        self.assertIsNone(rows[1]["dhi_ghi_ratio"])
        self.assertIsNone(rows[1]["dni_est_wm2"])

    def test_retroactive_configuration_applies_by_instant(self):
        early = ms(datetime(2026, 6, 21, 12, tzinfo=timezone.utc))
        late = early + 3_600_000
        versions = [
            {"id": 1, "valid_from_ms": 0, "config": {"site": SITE, "solys2_in_use": False, "instruments": []}},
            {"id": 2, "valid_from_ms": early + 1, "config": {"site": SITE, "solys2_in_use": True,
                                                              "instruments": [{"address": 4, "shaded": True}]}},
        ]
        rows = [record(4, 100, early), record(4, 100, late, cycle=1)]
        annotate_samples(rows, ConfigTimeline.build(versions, (4,)))
        self.assertEqual([r["role"] for r in rows], ["GHI", "DHI"])
        self.assertEqual([r["config_version"] for r in rows], [1, 2])

    def test_no_site_explains_why(self):
        rows = [record(3, 500, 0)]
        annotate_samples(rows, timeline(site=False))
        self.assertEqual(rows[0]["qc"]["ppl"], "na")
        self.assertIn("sitio sin configurar", rows[0]["qc"]["notes"][0])


class ConversionPropertyTests(unittest.TestCase):
    @FAST
    @given(st.integers(-32768, 32767))
    def test_s16_round_trip(self, value):
        self.assertEqual(to_s16(value & 0xFFFF), value)

    @FAST
    @given(st.integers(0, 0xFFFF), st.sampled_from([2, 1, 0, -1]))
    def test_documented_scales_are_exact_divisions(self, raw, scale):
        divisor = {2: 100.0, 1: 10.0, 0: 1.0, -1: 0.1}[scale]
        self.assertEqual(scale_sensor_value(raw, scale), to_s16(raw) / divisor)

    @FAST
    @given(st.integers(0, 0xFFFF), st.integers(-32768, 32767).filter(lambda s: s not in (2, 1, 0, -1)))
    def test_undocumented_scale_gives_no_value(self, raw, scale):
        self.assertIsNone(scale_sensor_value(raw, scale))


if __name__ == "__main__":
    unittest.main()

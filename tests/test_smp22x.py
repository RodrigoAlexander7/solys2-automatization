import csv
import io
import unittest
from datetime import datetime, timedelta, timezone

from smp22x import registers as reg
from smp22x.acquisition import _MinuteAggregator
from smp22x.cli import parse_duration
from smp22x.sensor import Sample, SMPSensor, pressure_to_hpa, scale_sensor_value, to_s16
from smp22x.transport import BusError, read_transaction_wire_time_s


class FakeBus:
    description = "fake"

    def __init__(self, regs: dict[int, int], fail: bool = False) -> None:
        self.regs = regs
        self.fail = fail
        self.coils: list[tuple[int, int, bool]] = []

    def read_input_registers(self, slave, start, count):
        if self.fail:
            raise BusError("NoResponseError: sin respuesta")
        return [self.regs.get(start + i, 0) for i in range(count)]

    def write_coil(self, slave, coil, value):
        self.coils.append((slave, coil, value))

    def reopen(self):
        pass

    def close(self):
        pass


def fast_regs() -> dict[int, int]:
    return {
        reg.IO_OPERATIONAL_MODE: reg.MODE_NORMAL,
        reg.IO_STATUS_FLAGS: 0,
        reg.IO_SCALE_FACTOR: 1,
        reg.IO_SENSOR1_DATA: 8123,
        reg.IO_RAW_SENSOR1_DATA: 8150,
        reg.IO_STDEV_SENSOR1: 15,
        reg.IO_BODY_TEMPERATURE: 253,
        reg.IO_EXT_POWER_SENSOR: 121,
        reg.IO_RH: 48,
        reg.IO_PRESSURE: 10125,
    }


class ConversionTests(unittest.TestCase):
    def test_to_s16(self):
        self.assertEqual(to_s16(0), 0)
        self.assertEqual(to_s16(32767), 32767)
        self.assertEqual(to_s16(0xFFFF), -1)
        self.assertEqual(to_s16(0x8000), -32768)

    def test_scale_factor_table(self):
        # §15.6: 2 -> /100, 1 -> /10, 0 -> x1, -1 -> x10
        self.assertAlmostEqual(scale_sensor_value(8123, 2), 81.23)
        self.assertAlmostEqual(scale_sensor_value(8123, 1), 812.3)
        self.assertAlmostEqual(scale_sensor_value(812, 0), 812.0)
        self.assertAlmostEqual(scale_sensor_value(81, -1), 810.0)
        self.assertAlmostEqual(scale_sensor_value(0xFFF6, 1), -1.0)  # offset nocturno negativo
        self.assertIsNone(scale_sensor_value(100, 3))

    def test_pressure_units(self):
        self.assertEqual(pressure_to_hpa(1013), 1013.0)
        self.assertEqual(pressure_to_hpa(10130), 1013.0)

    def test_decode_status(self):
        self.assertEqual(reg.decode_status((1 << 3) | (1 << 11)), ["ERROR", "DESICCANT_WARNING"])

    def test_wire_time_19200(self):
        # 8 B petición + 21 B respuesta a 11 bits/carácter + 2 x t3.5 (~2.0 ms)
        self.assertAlmostEqual(read_transaction_wire_time_s(8, 19200) * 1000, 20.6, places=1)

    def test_parse_duration(self):
        self.assertEqual(parse_duration("90"), 90)
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("2h"), 7200)


class SensorTests(unittest.TestCase):
    def test_read_sample_scaling(self):
        s = SMPSensor(FakeBus(fast_regs()), 2).read_sample(with_diagnostics=True)
        self.assertTrue(s.ok and s.valid)
        self.assertAlmostEqual(s.irradiance, 812.3)
        self.assertAlmostEqual(s.raw_irradiance, 815.0)
        self.assertAlmostEqual(s.stdev, 1.5)
        self.assertAlmostEqual(s.body_temp_c, 25.3)
        self.assertAlmostEqual(s.supply_v, 12.1)
        self.assertAlmostEqual(s.rh_pct, 4.8)
        self.assertAlmostEqual(s.pressure_hpa, 1012.5)

    def test_startup_flags_do_not_invalidate(self):
        regs = fast_regs()
        regs[reg.IO_STATUS_FLAGS] = (1 << 3) | (1 << 8)
        self.assertTrue(SMPSensor(FakeBus(regs), 2).read_sample().valid)

    def test_void_data_invalidates(self):
        regs = fast_regs()
        regs[reg.IO_STATUS_FLAGS] = 1 << 0
        self.assertFalse(SMPSensor(FakeBus(regs), 2).read_sample().valid)

    def test_non_normal_mode_invalidates(self):
        regs = fast_regs()
        regs[reg.IO_OPERATIONAL_MODE] = 5
        self.assertFalse(SMPSensor(FakeBus(regs), 2).read_sample().valid)

    def test_no_response(self):
        s = SMPSensor(FakeBus({}, fail=True), 3).read_sample()
        self.assertFalse(s.ok)
        self.assertIn("NoResponse", s.error)

    def test_clear_error_writes_coil_10(self):
        bus = FakeBus(fast_regs())
        SMPSensor(bus, 4).clear_error()
        self.assertEqual(bus.coils, [(4, 10, True)])


class MinuteAggregatorTests(unittest.TestCase):
    def test_minute_rollover(self):
        out = io.StringIO()
        agg = _MinuteAggregator(csv.writer(out), interval_s=1.0)
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(61):
            s = Sample(2, t0 + timedelta(seconds=i), ok=True, mode=1, status=0,
                       irradiance=100.0 + (i % 2), body_temp_c=25.0)
            agg.add(s)
        agg.flush()
        rows = list(csv.reader(io.StringIO(out.getvalue())))
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first[3], "60")          # n_samples
        self.assertEqual(first[5], "100.50")      # mean
        self.assertEqual(first[10], "1")          # complete
        self.assertEqual(rows[1][10], "0")        # minuto parcial


if __name__ == "__main__":
    unittest.main()

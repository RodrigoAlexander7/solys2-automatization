import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from smp22x import acquisition
from smp22x import registers as reg
from smp22x.acquisition import Acquisition, AcquisitionConfig, _MinuteAggregator
from smp22x.cli import _open_bus, build_parser, cmd_scan, parse_duration, sensor_types
from smp22x.sensor import (
    LW_CHECK_TOLERANCE_WM2,
    Sample,
    SMPSensor,
    pressure_to_hpa,
    scale_sensor_value,
    to_s16,
)
from smp22x.simulator import SimulatedBus
from smp22x.transport import BusError, PortError, read_transaction_wire_time_s


class FakeBus:
    description = "fake"

    def __init__(
        self,
        regs: dict[int, int],
        fail: bool = False,
        baudrate: int = reg.DEFAULT_BAUDRATE,
        parity: str = reg.DEFAULT_PARITY,
        port_error_once_for: int | None = None,
    ) -> None:
        self.regs = regs
        self.fail = fail
        self.baudrate = baudrate
        self.parity = parity
        self.port_error_once_for = port_error_once_for
        self.coils: list[tuple[int, int, bool]] = []
        self.requests: list[tuple[int, int, int]] = []

    def read_input_registers(self, slave, start, count):
        self.requests.append((slave, start, count))
        if self.fail:
            raise BusError("NoResponseError: sin respuesta")
        if slave == self.port_error_once_for and start == reg.FAST_BLOCK_START:
            self.port_error_once_for = None
            raise PortError("USB desconectado (prueba)")
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


def sgr_regs() -> dict[int, int]:
    """Pirgeómetro con escala 1: neta −130,2 W/m², 25,3 °C → σT⁴ = 449,9 → Ld↓ ≈ 319,7."""
    regs = fast_regs()
    regs[reg.IO_SENSOR1_DATA] = -1302 & 0xFFFF
    regs[reg.IO_RAW_SENSOR1_DATA] = -1298 & 0xFFFF
    regs[reg.IO_SENSOR2_DATA] = 3197
    regs[reg.IO_RAW_SENSOR2_DATA] = 3201
    return regs


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

    def test_wire_time_without_parity_uses_10_bits(self):
        # 8N1: 29 B x 10 bits + 2 x t3.5 (1,82 ms) = 18,75 ms
        self.assertAlmostEqual(read_transaction_wire_time_s(8, 19200, "N") * 1000, 18.75, places=2)

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

    def test_pyranometer_reads_block_2_to_9(self):
        bus = FakeBus(fast_regs())
        s = SMPSensor(bus, 3).read_sample()
        self.assertEqual(bus.requests, [(3, 2, 8)])
        self.assertIsNone(s.lw_down)
        self.assertEqual(s.measurement, s.irradiance)

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


class PyrgeometerTests(unittest.TestCase):
    def test_reads_block_2_to_11(self):
        bus = FakeBus(sgr_regs())
        SMPSensor(bus, 2, reg.PYRGEOMETER).read_sample()
        self.assertEqual(bus.requests, [(2, 2, 10)])

    def test_net_and_lw_down(self):
        s = SMPSensor(FakeBus(sgr_regs()), 2, reg.PYRGEOMETER).read_sample()
        self.assertTrue(s.ok and s.valid)
        self.assertAlmostEqual(s.irradiance, -130.2)   # IO_SENSOR1_DATA = neta
        self.assertAlmostEqual(s.lw_down, 319.7)       # IO_SENSOR2_DATA = Ld↓
        self.assertAlmostEqual(s.raw_lw_down, 320.1)
        self.assertEqual(s.measurement, s.lw_down)

    def test_lw_down_matches_net_plus_sigma_t4(self):
        s = SMPSensor(FakeBus(sgr_regs()), 2, reg.PYRGEOMETER).read_sample()
        self.assertAlmostEqual(s.lw_down_calc, 319.65, places=1)
        self.assertLess(abs(s.lw_down - s.lw_down_calc), LW_CHECK_TOLERANCE_WM2)

    def test_failed_read_keeps_sensor_type(self):
        s = SMPSensor(FakeBus({}, fail=True), 2, reg.PYRGEOMETER).read_sample()
        self.assertEqual(s.sensor_type, reg.PYRGEOMETER)
        self.assertFalse(s.valid)

    def test_device_type_611_configured_as_pyranometer_warns(self):
        regs = sgr_regs()
        regs[reg.IO_DEVICE_TYPE] = 611
        regs[reg.IO_NODE_ID] = 2
        info = SMPSensor(FakeBus(regs), 2, reg.PYRANOMETER).read_info()
        self.assertEqual(info.model, "SGR4")
        self.assertTrue(any("--pyrgeometers" in w for w in info.warnings))

    def test_device_type_611_configured_as_pyrgeometer(self):
        regs = sgr_regs()
        regs[reg.IO_DEVICE_TYPE] = 611
        regs[reg.IO_NODE_ID] = 2
        info = SMPSensor(FakeBus(regs), 2, reg.PYRGEOMETER).read_info()
        self.assertFalse(any("--pyrgeometers" in w for w in info.warnings))
        self.assertTrue(any("no figura en el manual V20" in w for w in info.warnings))

    def test_simulated_sgr4_is_consistent(self):
        bus = SimulatedBus([2, 3], failure_rate=0, seed=1, pyrgeometers=[2])
        s = SMPSensor(bus, 2, reg.PYRGEOMETER).read_sample()
        self.assertLess(s.irradiance, 0)  # cielo más frío que el sensor
        self.assertLess(abs(s.lw_down - s.lw_down_calc), 1.0)


class MinuteAggregatorTests(unittest.TestCase):
    def test_minute_rollover(self):
        out = io.StringIO()
        agg = _MinuteAggregator(csv.writer(out), interval_s=1.0)
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(61):
            s = Sample(3, t0 + timedelta(seconds=i), ok=True, mode=1, status=0,
                       irradiance=100.0 + (i % 2), body_temp_c=25.0)
            agg.add(s)
        agg.flush()
        rows = list(csv.reader(io.StringIO(out.getvalue())))
        self.assertEqual(len(rows), 2)
        first = dict(zip(acquisition.MINUTE_COLUMNS, rows[0]))
        self.assertEqual(first["sensor_type"], reg.PYRANOMETER)
        self.assertEqual(first["n_samples"], "60")
        self.assertEqual(first["mean_wm2"], "100.50")
        self.assertEqual(first["net_mean_wm2"], "")
        self.assertEqual(first["complete"], "1")
        self.assertEqual(dict(zip(acquisition.MINUTE_COLUMNS, rows[1]))["complete"], "0")

    def test_pyrgeometer_averages_lw_down(self):
        out = io.StringIO()
        agg = _MinuteAggregator(csv.writer(out), interval_s=1.0)
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(60):
            s = Sample(2, t0 + timedelta(seconds=i), ok=True, sensor_type=reg.PYRGEOMETER,
                       mode=1, status=0, irradiance=-130.0, lw_down=320.0, body_temp_c=25.0)
            agg.add(s)
        agg.flush()
        row = dict(zip(acquisition.MINUTE_COLUMNS, next(csv.reader(io.StringIO(out.getvalue())))))
        self.assertEqual(row["sensor_type"], reg.PYRGEOMETER)
        self.assertEqual(row["mean_wm2"], "320.00")
        self.assertEqual(row["net_mean_wm2"], "-130.00")


def _quiet_run(bus, cfg) -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        run_dir = Acquisition(bus, cfg).run()
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


class BugRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def test_run_json_wire_time_uses_bus_baudrate(self):
        # Bug 6: antes se calculaba siempre a 19200 (20,6 ms) aunque el bus fuera a 9600
        bus = FakeBus(fast_regs(), baudrate=9600)
        cfg = AcquisitionConfig(addresses=[2, 3], pyrgeometers=[2], out_dir=self.out, live=False)
        acq = Acquisition(bus, cfg)
        acq.run_dir.mkdir(parents=True)
        acq._write_meta(datetime.now(timezone.utc), None, "test")
        meta = json.loads(acq.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["bus_timing"]["baudrate"], 9600)
        self.assertEqual(meta["per_sensor"]["3"]["theoretical_wire_ms_per_read"], 41.2)
        self.assertEqual(meta["per_sensor"]["2"]["theoretical_wire_ms_per_read"], 45.8)
        self.assertEqual(meta["bus_timing"]["theoretical_wire_ms_per_cycle"], 87.1)

    def test_port_error_is_not_a_comm_error(self):
        # Bug 7: un PortError sumaba una petición sin respuesta al sensor de turno
        bus = FakeBus(fast_regs(), port_error_once_for=3)
        cfg = AcquisitionConfig(addresses=[2, 3, 4], interval_s=0.05, duration_s=0.4,
                                out_dir=self.out, live=False, clear_errors=False)
        with mock.patch.object(acquisition, "PORT_RETRY_S", 0.0):
            meta = _quiet_run(bus, cfg)
        for address in ("2", "3", "4"):
            st = meta["per_sensor"][address]
            self.assertEqual(st["comm_errors"], 0, address)
            self.assertEqual(st["requests"], st["responses_ok"], address)
        self.assertEqual(meta["bus_timing"]["port_errors"], 1)
        self.assertEqual(meta["bus_timing"]["overruns"], 0)

    def test_scan_does_not_mutate_args(self):
        # Bug 8 y simulador: antes args acababa en 115200 O y se "encontraban"
        # 15 sensores en una sola dirección
        args = build_parser().parse_args(
            ["scan", "--simulate", "--all-baud", "--first", "3", "--last", "3", "--timeout", "0.001"]
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_scan(args)
        self.assertEqual((args.baud, args.parity), (19200, "E"))
        self.assertEqual(code, 0)
        self.assertIn("1 sensor(es) encontrado(s)", out.getvalue())

    def test_open_bus_override_keeps_args(self):
        args = build_parser().parse_args(["scan", "--simulate"])
        bus = _open_bus(args, baud=9600, parity="N")
        self.assertEqual((bus.baudrate, bus.parity), (9600, "N"))
        self.assertIn("9600 baud 8N1", bus.description)
        self.assertEqual((args.baud, args.parity), (19200, "E"))

    def test_simulator_only_answers_factory_settings(self):
        bus = SimulatedBus([2], baudrate=9600, timeout=0.001)
        with self.assertRaises(BusError):
            bus.read_input_registers(2, reg.FAST_BLOCK_START, reg.FAST_BLOCK_COUNT)


class CliTests(unittest.TestCase):
    def test_default_sensor_types(self):
        args = build_parser().parse_args(["log"])
        self.assertEqual(sensor_types(args), {2: reg.PYRGEOMETER, 3: reg.PYRANOMETER, 4: reg.PYRANOMETER})

    def test_no_pyrgeometers(self):
        args = build_parser().parse_args(["log", "--pyrgeometers"])
        self.assertEqual(set(sensor_types(args).values()), {reg.PYRANOMETER})

    def test_pyrgeometer_must_be_in_addresses(self):
        args = build_parser().parse_args(["log", "-a", "3", "4", "--pyrgeometers", "2"])
        with self.assertRaises(SystemExit):
            sensor_types(args)


if __name__ == "__main__":
    unittest.main()

"""Bajo consumo (R28, R30; C19, C20): terminal legible, trazas a archivo y series reducidas."""
import contextlib
import io
import json
import logging
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from smp22x import console, tracing
from smp22x.acquisition import Acquisition, AcquisitionConfig
from smp22x.api import create_app
from smp22x.config import Settings
from smp22x.decimate import decimate
from smp22x.events import Event
from smp22x.sensor import Sample
from scenario import SUMMER_DAY, simulate
from test_smp22x import FakeBus, fast_regs, sgr_regs

T0 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def sample(address=3, value=812.34, ok=True, status=0, pyrg=False):
    return Sample(address, T0, ok=ok, mode=1 if ok else None, status=status if ok else None, scale_factor=1,
                  sensor_type="pyrgeometer" if pyrg else "pyranometer",
                  irradiance=None if pyrg else value, lw_down=value if pyrg else None, body_temp_c=25.0,
                  error="" if ok else "NoResponseError: sin respuesta")


class ConsoleTests(unittest.TestCase):
    def test_number_format(self):
        self.assertEqual(console.num(812.34), "  812,3")
        self.assertEqual(console.num(None), "    ---")
        self.assertEqual(console.num(-0.04), "   -0,0")

    def test_cycle_line_is_readable(self):
        line = console.cycle_line([sample(2, 320.4, pyrg=True), sample(3), sample(4, ok=False), sample(5, status=1)],
                                  62.4, datetime(2026, 6, 21, 12, 4, 31))
        self.assertTrue(line.startswith("12:04:31  #2 Ld↓   320,4   #3   812,3   #4     ---   #5     INV"))
        self.assertIn("W/m²   ciclo   62 ms", line)
        self.assertIn("#4 sin respuesta", line)
        self.assertIn("#5 inválida (VOID_DATA)", line)
        self.assertNotIn("{", line)

    def test_event_lines(self):
        t = int(T0.timestamp() * 1000)
        warning = console.event_line(Event(t, "flags_changed", "warning", 3, {"set": ["DESICCANT_WARNING"], "names": ["DESICCANT_WARNING"]}))
        self.assertIn("AVISO", warning)
        self.assertIn("#3 flags: activados DESICCANT_WARNING", warning)
        self.assertIn("ERROR", console.event_line(Event(t, "port_lost", "error", None, {"error": "USB"})))
        clock = console.event_line(Event(t, "clock_check", "info", None, {"offset_s": 0.012, "delay_s": 0.03, "server": "pool.ntp.org"}))
        self.assertIn("Reloj: desfase +0.012 s", clock)

    def test_summary_every_n_seconds(self):
        summary = console.Summary(10)
        lines = [summary.add([sample(3, 800 + i)], 60, float(i)) for i in range(12)]
        printed = [line for line in lines if line]
        self.assertEqual(len(printed), 1)
        self.assertIn("media 10 s", printed[0])
        self.assertIn("#3   805,0", printed[0])
        self.assertIn("válidas 11/11", printed[0])

    def test_acquisition_prints_lines_and_events_not_json(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = AcquisitionConfig(addresses=[2, 3], pyrgeometers=[2], interval_s=0.05, duration_s=0.3,
                                    out_dir=Path(directory), live=True, db_path=Path(directory) / "a.db")
            bus = FakeBus({**sgr_regs()})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                Acquisition(bus, cfg).run()
        text = out.getvalue()
        self.assertIn("#2 Ld↓", text)
        self.assertIn("INFO   #2: IO_CLEAR_ERROR enviado", text)
        self.assertNotIn('"operation"', text)

    def test_summary_mode_in_acquisition(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = AcquisitionConfig(addresses=[3], interval_s=0.05, duration_s=0.5, out_dir=Path(directory),
                                    live=True, db_path=Path(directory) / "a.db", summary_every_s=0.2, clear_errors=False)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                Acquisition(FakeBus(fast_regs()), cfg).run()
        cycle_lines = [l for l in out.getvalue().splitlines() if re.match(r"\d\d:\d\d:\d\d  #", l)]
        self.assertEqual(cycle_lines, [])
        self.assertTrue(any("media 0.2 s" in l for l in out.getvalue().splitlines()))


class TraceOutputTests(unittest.TestCase):
    def tearDown(self):
        tracing.configure_logging("INFO", "json")
        for handler in list(tracing.logger.handlers):
            tracing.logger.removeHandler(handler)

    def capture(self, fmt, level, log_file=None):
        tracing.configure_logging(level, fmt, log_file)
        stream = io.StringIO()
        for handler in tracing.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setStream(stream)
        return stream

    def test_text_mode_keeps_console_clean_and_json_in_file(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = str(Path(directory) / "logs" / "log.jsonl")
            stream = self.capture("texto", "INFO", log_file)
            tracing.emit("SMPSensor.read_sample", "OK", 12.0)
            tracing.emit("Modbus.read", "FAILED", 300.0, address=3, error="communication_error", message="timeout")
            tracing.emit("Acquisition.run", "FAILED", 1.0, error="StorageFailure", message="disco lleno",
                         traceback="Traceback ...")
            tracing.emit("HTTP", "FAILED", 1.0, http_status=500)
            tracing.emit("HTTP", "FAILED", 1.0, http_status=422)
            lines = stream.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("ERROR  Acquisition.run: disco lleno", lines[0])
            self.assertIn("detalle en", lines[0])
            self.assertIn("HTTP 500", lines[1])
            for handler in tracing.logger.handlers:
                handler.flush()
            records = [json.loads(l) for l in Path(log_file).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 5)
            self.assertEqual(records[1]["error"], "communication_error")

    def test_default_level_writes_only_failures_and_skips_serialization(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = str(Path(directory) / "log.jsonl")
            self.capture("texto", "WARNING", log_file)
            self.assertFalse(tracing.logger.isEnabledFor(logging.INFO))
            tracing.emit("SMPSensor.read_sample", "OK", 1.0)
            tracing.emit("Modbus.read", "FAILED", 1.0, error="communication_error")
            for handler in tracing.logger.handlers:
                handler.flush()
            records = Path(log_file).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)

    def test_json_mode_restores_previous_behaviour(self):
        stream = self.capture("json", "INFO")
        tracing.emit("SMPSensor.read_sample", "OK", 1.0)
        self.assertEqual(json.loads(stream.getvalue())["operation"], "SMPSensor.read_sample")


def rows(values, start=0, step=1000, address=3):
    return [{"address": address, "role": "GHI", "t_ms": start + i * step, "measurement": v, "valid": v is not None,
             "qc": "ok", "qc_failed": [], "dni_est_wm2": None} for i, v in enumerate(values)]


class DecimationTests(unittest.TestCase):
    def test_small_series_is_untouched(self):
        data = rows([1.0, 2.0, 3.0])
        out, bucket, reduced = decimate(data, time_key="t_ms", value_key="measurement", valid_key="valid",
                                        start_ms=0, end_ms=3000, max_points=600, base_step_ms=1000)
        self.assertFalse(reduced)
        self.assertIs(out, data)
        self.assertEqual(bucket, 1000)

    def test_bounded_and_keeps_extremes(self):
        values = [100.0] * 10_000
        values[1234] = 999.0  # pico aislado (por ejemplo, realce por borde de nube)
        values[7000] = -5.0
        out, bucket, reduced = decimate(rows(values), time_key="t_ms", value_key="measurement", valid_key="valid",
                                        start_ms=0, end_ms=10_000_000, max_points=600, base_step_ms=1000)
        self.assertTrue(reduced)
        self.assertLessEqual(len(out), 600)
        kept = [r["measurement"] for r in out]
        self.assertIn(999.0, kept)
        self.assertIn(-5.0, kept)
        self.assertEqual(bucket % 1000, 0)

    def test_empty_buckets_become_gaps(self):
        values = [10.0] * 3000 + [None] * 4000 + [20.0] * 3000
        out, bucket, _ = decimate(rows(values), time_key="t_ms", value_key="measurement", valid_key="valid",
                                  start_ms=0, end_ms=10_000_000, max_points=600, base_step_ms=1000)
        middle = [r for r in out if 3_200_000 < r["t_ms"] < 6_800_000]
        self.assertTrue(middle and all(r["measurement"] is None for r in middle))

    def test_series_endpoint_reduces_only_when_asked(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "a.sqlite3"
            simulate(db, start=SUMMER_DAY, hours=24, step_s=60)
            client = TestClient(create_app(Settings(db=db, frontend=Path(directory), observatory=Path(directory) / "o.db")),
                                base_url="http://127.0.0.1:8050")
            params = {"start": SUMMER_DAY.isoformat(), "end": (SUMMER_DAY + timedelta(days=1)).isoformat()}
            full = client.get("/api/series", params=params).json()
            small = client.get("/api/series", params={**params, "max_points": 200}).json()
        self.assertFalse(full["decimated"])
        self.assertEqual(len(full["items"]), 1440 * 3)
        self.assertTrue(small["decimated"])
        per_address = {a: sum(1 for r in small["items"] if r["address"] == a) for a in (2, 3, 4)}
        self.assertTrue(all(n <= 200 for n in per_address.values()), per_address)
        self.assertGreater(small["bucket_ms"], 60_000)
        top = max(r["mean_wm2"] for r in full["items"] if r["address"] == 3 and r["mean_wm2"] is not None)
        self.assertEqual(top, max(r["mean_wm2"] for r in small["items"] if r["address"] == 3 and r["mean_wm2"] is not None))


if __name__ == "__main__":
    unittest.main()

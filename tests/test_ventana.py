"""Ventana liviana (R29; C17, C18). La interfaz se prueba con la ventana oculta."""
import csv
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from smp22x import storage, ventana
from smp22x.acquisition import RAW_COLUMNS
from smp22x.archive import run_once
from smp22x.minutes import MINUTE_COLUMNS
from smp22x.result import require
from smp22x.simulator import Faults
from smp22x.ventana import (ExportError, LiveSource, SourceError, export_csv, format_local, nice_ticks, parse_local,
                            reading_state, segments)
from scenario import SUMMER_DAY, simulate
from test_storage_v2 import legacy_db

ROOT = Path(__file__).resolve().parent.parent


class HelperTests(unittest.TestCase):
    def test_schema_version_matches_storage(self):
        self.assertEqual(ventana.SCHEMA_VERSION, storage.SCHEMA_VERSION)

    def test_segments_break_on_gaps_and_invalid(self):
        points = [(0, 1.0), (1000, 2.0), (2000, None), (3000, 4.0), (10_000, 5.0), (11_000, 6.0)]
        self.assertEqual(segments(points), [[(0, 1.0), (1000, 2.0)], [(3000, 4.0)], [(10_000, 5.0), (11_000, 6.0)]])
        self.assertEqual(segments([]), [])

    def test_nice_ticks(self):
        self.assertEqual(nice_ticks(0, 100, 4), [0, 25, 50, 75, 100])
        self.assertGreaterEqual(len(nice_ticks(560, 1000)), 4)
        ticks = nice_ticks(317.3, 323.9)
        self.assertTrue(all(317.3 <= t <= 323.9 for t in ticks) and len(ticks) >= 3)
        self.assertEqual(nice_ticks(5, 5), [5])

    def test_local_dates_round_trip(self):
        t = int(datetime(2026, 6, 21, 10, 30).astimezone().timestamp() * 1000)
        self.assertEqual(parse_local(format_local(t)), t)
        with self.assertRaises(ValueError):
            parse_local("21/06/2026")

    def test_reading_state(self):
        base = {"ok": True, "valid": True, "t_ms": 0}
        self.assertEqual(reading_state(base, 1000), "Válida")
        self.assertEqual(reading_state(base, 10_000), "Anterior")
        self.assertEqual(reading_state({**base, "valid": False}, 1000), "Inválida")
        self.assertEqual(reading_state({**base, "ok": False}, 1000), "Sin respuesta")

    def test_no_heavy_imports(self):
        code = ("import sys, smp22x.ventana; "
                "print(sorted(m for m in ('pandas','numpy','matplotlib','pyarrow','fastapi') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "[]")


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "acq.sqlite3"

    def test_missing_database_and_old_schema(self):
        with self.assertRaisesRegex(SourceError, "Esperando"):
            LiveSource(self.db).snapshot(0)
        legacy_db(self.db)
        with self.assertRaisesRegex(SourceError, "migrate"):
            LiveSource(self.db).snapshot(0)

    def test_last_minute_only(self):
        info = simulate(self.db, hours=0.1, step_s=1, shaded=())
        source = LiveSource(self.db)
        self.addCleanup(source.close)
        snap = source.snapshot(info["end_ms"])
        self.assertEqual(set(snap.series), {2, 3, 4})
        self.assertTrue(all(len(s["points"]) <= 61 for s in snap.series.values()))
        self.assertTrue(all(t >= info["end_ms"] - 60_000 for s in snap.series.values() for t, _ in s["points"]))
        self.assertEqual(set(snap.latest), {2, 3, 4})
        self.assertEqual(snap.series[2]["type"], "pyrgeometer")
        self.assertTrue(snap.run["simulated"])

    def test_stopped_acquisition_shows_last_known_values(self):
        info = simulate(self.db, hours=0.05, step_s=1)
        source = LiveSource(self.db)
        self.addCleanup(source.close)
        later = info["end_ms"] + 10 * 60_000
        snap = source.snapshot(later)
        self.assertEqual(snap.series, {})
        self.assertEqual(set(snap.latest), {2, 3, 4})
        self.assertEqual(reading_state(snap.latest[3], later), "Anterior")

    def test_silent_sensor_appears_as_without_response(self):
        info = simulate(self.db, hours=0.05, step_s=1, faults={3: Faults(silent=(0, 10_000))})
        source = LiveSource(self.db)
        self.addCleanup(source.close)
        snap = source.snapshot(info["end_ms"])
        self.assertTrue(all(v is None for _, v in snap.series[3]["points"]))
        self.assertEqual(reading_state(snap.latest[3], info["end_ms"]), "Sin respuesta")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "acq.sqlite3"
        self.archive = self.dir / "archive"
        self.info = simulate(self.db, start=SUMMER_DAY, hours=48, step_s=600, faults={3: Faults(void_every_s=1800)})

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_one_second_classic_columns(self):
        path = self.dir / "datos.csv"
        rows = export_csv(self.db, self.archive, path, self.info["start_ms"], self.info["start_ms"] + 86_400_000,
                          None, "1s", True)
        table = self.read(path)
        self.assertEqual(table[0], RAW_COLUMNS)
        self.assertEqual(len(table) - 1, rows)
        self.assertEqual(rows, 144 * 3)

    def test_filters_invalid_and_instruments(self):
        path = self.dir / "datos.csv"
        everything = export_csv(self.db, self.archive, path, self.info["start_ms"], self.info["end_ms"], [3], "1s", True)
        valid = export_csv(self.db, self.archive, path, self.info["start_ms"], self.info["end_ms"], [3], "1s", False)
        self.assertLess(valid, everything)
        table = self.read(path)
        self.assertEqual({r[3] for r in table[1:]}, {"3"})
        self.assertEqual({r[17] for r in table[1:]}, {"1"})  # columna valid

    def test_minutes_classic_columns(self):
        path = self.dir / "minutos.csv"
        rows = export_csv(self.db, self.archive, path, self.info["start_ms"], self.info["end_ms"], None, "1min", True)
        table = self.read(path)
        self.assertEqual(table[0], MINUTE_COLUMNS)
        self.assertEqual(rows, len(table) - 1)
        self.assertGreater(rows, 0)

    def test_limits(self):
        path = self.dir / "x.csv"
        for args in ((10, 5, "1s"), (0, 32 * 86_400_000, "1s"), (0, 367 * 86_400_000, "1min"), (0, 10, "5s")):
            with self.assertRaises(ExportError):
                export_csv(self.db, self.archive, path, args[0], args[1], None, args[2], True)
        self.assertFalse(path.exists())

    def test_failed_export_leaves_no_partial_file(self):
        path = self.dir / "roto.csv"
        with mock.patch("smp22x.acquisition.raw_row", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                export_csv(self.db, self.archive, path, self.info["start_ms"], self.info["end_ms"], None, "1s", True)
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name(path.name + ".part").exists())

    def test_archived_days_export_the_same(self):
        before, after = self.dir / "antes.csv", self.dir / "despues.csv"
        export_csv(self.db, self.archive, before, self.info["start_ms"], self.info["end_ms"], None, "1s", True)
        report = require(run_once(self.db, self.archive, 30,
                                          int((SUMMER_DAY + timedelta(days=31, hours=3)).timestamp() * 1000)))
        self.assertTrue(report["pruned"])
        export_csv(self.db, self.archive, after, self.info["start_ms"], self.info["end_ms"], None, "1s", True)
        self.assertEqual(before.read_text(encoding="utf-8"), after.read_text(encoding="utf-8"))


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
        except Exception as exc:  # sin pantalla o sin Tk
            raise unittest.SkipTest(f"Tk no disponible: {exc}")
        cls.root.withdraw()  # nunca se muestra en el escritorio

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "acq.sqlite3"
        self.info = simulate(self.db, hours=0.1, step_s=1)
        self.now = self.info["end_ms"] / 1000
        import tkinter as tk
        self.top = tk.Toplevel(self.root)
        self.top.withdraw()
        self.addCleanup(self.top.destroy)
        self.window = ventana.LightWindow(self.top, LiveSource(self.db), self.db, self.dir / "archive",
                                          clock=lambda: self.now)
        self.addCleanup(self.window.source.close)

    def test_refresh_fills_charts_and_table(self):
        self.window.refresh()
        self.assertEqual(sorted(self.window.table.get_children()), ["2", "3", "4"])
        values = self.window.table.item("2")["values"]
        self.assertIn("pirgeómetro", values[1])
        self.assertGreater(len(self.window.shortwave.canvas.find_all()), 10)
        self.assertGreater(len(self.window.longwave.canvas.find_all()), 10)
        self.assertIn("SIMULADO", self.window.status.get())

    def test_minimized_skips_work_and_restore_redraws(self):
        calls = []
        real = self.window.source.snapshot
        self.window.source.snapshot = lambda now: calls.append(now) or real(now)
        with mock.patch.object(self.window, "minimized", return_value=True):
            self.window.tick()
        self.assertEqual(calls, [])
        self.window._on_map(SimpleNamespace(widget=self.top))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.window.table.get_children()), 3)

    def test_database_error_is_shown_not_raised(self):
        window = ventana.LightWindow(self.top, LiveSource(self.dir / "no-existe.sqlite3"), self.db, self.dir,
                                     clock=lambda: self.now)
        window.refresh()
        self.assertIn("Esperando", window.status.get())

    def test_save_csv_from_the_form(self):
        self.window.refresh()
        self.window.start_var.set(format_local(self.info["start_ms"] - 60_000))
        self.window.end_var.set(format_local(self.info["end_ms"] + 60_000))
        self.window.resolution.set("1s")
        self.window.instrument_vars[2].set(False)
        path = self.dir / "ventana.csv"
        self.window.save_csv(str(path))
        deadline = time.monotonic() + 20
        while "Guardado" not in self.window.export_status.get() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.05)
        self.assertIn("Guardado", self.window.export_status.get())
        with open(path, encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual({r[3] for r in rows[1:]}, {"3", "4"})

    def test_form_validation(self):
        self.window.start_var.set("ayer")
        self.window.save_csv(str(self.dir / "x.csv"))
        self.assertIn("AAAA-MM-DD", self.window.export_status.get())
        self.window.start_var.set("2026-06-21 12:00")
        self.window.end_var.set("2026-06-21 11:00")
        self.window.save_csv(str(self.dir / "x.csv"))
        self.assertIn("anterior", self.window.export_status.get())


if __name__ == "__main__":
    unittest.main()

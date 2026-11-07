"""Rendimiento con volumen real (R11, R22, R27). Umbrales holgados: detectan regresiones de orden de magnitud.

Un día sintético a 1 s (3 sensores, 259 200 filas) corre siempre. El de 30 días
se activa con SMP_PERF=1 porque tarda minutos. Las cifras dependen del equipo:
la medición en la mini-PC Windows sigue siendo aceptación externa.
"""
import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from smp22x import registers as reg
from smp22x.acquisition import Acquisition, AcquisitionConfig
from smp22x.api import create_app
from smp22x.config import Settings
from smp22x.events import to_ms
from smp22x.minutes import MinuteAggregator
from smp22x.result import require
from smp22x.sensor import Sample
from smp22x.storage import MINUTE_FIELDS, SAMPLE_COLUMNS, Reader, Writer, minute_params, sample_params
from test_smp22x import FakeBus, fast_regs

START = datetime(2026, 6, 21, tzinfo=timezone.utc)


def synthetic(db: Path, days: int) -> int:
    """Inserta días de muestras a 1 s en transacciones grandes (solo para medir lecturas)."""
    writer = Writer(db)
    require(writer.open())
    require(writer.start("perf", {"config": {"interval_s": 1, "addresses": [2, 3, 4], "pyrgeometers": [2]}}))
    pk = writer.run_pks["perf"]
    agg = MinuteAggregator(1)
    sql = f"INSERT INTO samples({','.join(SAMPLE_COLUMNS)}) VALUES({','.join('?' * len(SAMPLE_COLUMNS))})"
    msql = f"INSERT INTO minutes({','.join(MINUTE_FIELDS)}) VALUES({','.join('?' * len(MINUTE_FIELDS))})"
    total = 0
    for cycle in range(days * 86_400):
        t = START + timedelta(seconds=cycle)
        samples = [
            Sample(2, t, ok=True, sensor_type=reg.PYRGEOMETER, mode=1, status=0, scale_factor=1, irradiance=-120.0,
                   lw_down=320.0 + (cycle % 7), stdev=0.1, body_temp_c=24.0, supply_v=12.0, rtt_ms=22.0,
                   raw_registers={2: 1, 3: 0, 4: 1, 5: 64336, 6: 64330, 7: 1, 8: 240, 9: 120, 10: 3200, 11: 3198}),
            *(Sample(a, t, ok=True, mode=1, status=0, scale_factor=1, irradiance=500.0 + (cycle % 11), raw_irradiance=501.0,
                     stdev=0.2, body_temp_c=25.0, supply_v=12.0, rtt_ms=20.0,
                     raw_registers={2: 1, 3: 0, 4: 1, 5: 5000, 6: 5010, 7: 2, 8: 250, 9: 120}) for a in (3, 4)),
        ]
        if cycle % 20_000 == 0:
            if cycle:
                writer.connection.commit()
            writer.connection.execute("BEGIN")
        writer.connection.executemany(sql, [sample_params(s, pk, cycle) for s in samples])
        writer.connection.executemany(msql, [minute_params(m, pk) for s in samples for m in agg.add(s)])
        total += 3
    writer.connection.executemany(msql, [minute_params(m, pk) for m in agg.flush()])
    writer.connection.commit()
    writer.close()
    return total


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - start


class OneDayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db = root / "perf.sqlite3"
        cls.rows = synthetic(cls.db, 1)
        cls.client = TestClient(create_app(Settings(db=cls.db, frontend=root, observatory=root / "o.db",
                                                    archive_dir=root / "archive")), base_url="http://127.0.0.1:8050")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_size_per_row(self):
        per_row = self.db.stat().st_size / self.rows
        self.assertLess(per_row, 320, f"{per_row:.0f} B/fila")

    def test_one_hour_query(self):
        start = to_ms(START + timedelta(hours=12))
        page, seconds = timed(lambda: require(Reader(self.db).range_samples(start, start + 3_600_000, limit=20_000)))
        self.assertEqual(len(page["items"]), 10_800)
        self.assertLess(seconds, 2.0)

    def test_series_endpoint_one_hour_at_1s(self):
        start = START + timedelta(hours=12)
        response, seconds = timed(lambda: self.client.get("/api/series", params={
            "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}))
        self.assertEqual(len(response.json()["items"]), 10_800)
        self.assertLess(seconds, 5.0)

    def test_minutes_for_the_day(self):
        response, seconds = timed(lambda: self.client.get("/api/minutes", params={
            "start": START.isoformat(), "end": (START + timedelta(days=1)).isoformat()}))
        self.assertEqual(len(response.json()["items"]), 1440 * 3)
        self.assertLess(seconds, 3.0)

    def test_export_full_day_at_1s(self):
        response, seconds = timed(lambda: self.client.get("/api/export", params={
            "start": START.isoformat(), "end": (START + timedelta(days=1)).isoformat(), "resolution": "1s"}))
        self.assertEqual(response.text.count("\n"), self.rows + 1)
        self.assertLess(seconds, 120)


class AcquisitionUnderWebLoadTests(unittest.TestCase):
    def test_reads_do_not_delay_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "acq.sqlite3"
            cfg = AcquisitionConfig(addresses=[2, 3, 4], pyrgeometers=[2], interval_s=0.05, duration_s=2.0,
                                    out_dir=root / "out", live=False, db_path=db, clear_errors=False)
            stop = threading.Event()
            client_holder = {}

            def hammer():
                while not db.exists() and not stop.is_set():
                    time.sleep(0.01)
                client = TestClient(create_app(Settings(db=db, frontend=root, observatory=root / "o.db")),
                                    base_url="http://127.0.0.1:8050")
                client_holder["requests"] = 0
                while not stop.is_set():
                    client.get("/api/live")
                    client.get("/api/series")
                    client_holder["requests"] += 2

            thread = threading.Thread(target=hammer, daemon=True)
            thread.start()
            with contextlib.redirect_stdout(io.StringIO()):
                acq = Acquisition(FakeBus(fast_regs()), cfg)
                acq.run()
            stop.set()
            thread.join(timeout=5)
            self.assertGreater(client_holder.get("requests", 0), 10)
            self.assertLessEqual(acq.overruns, 3)


@unittest.skipUnless(os.environ.get("SMP_PERF") == "1", "SMP_PERF=1 para el volumen de 30 días")
class ThirtyDayTests(unittest.TestCase):
    def test_thirty_days(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "perf.sqlite3"
            rows = synthetic(db, 30)
            print(f"\n30 días: {rows} filas, {db.stat().st_size / 1e9:.2f} GB, {db.stat().st_size / rows:.0f} B/fila")
            start = to_ms(START + timedelta(days=15))
            _, seconds = timed(lambda: require(Reader(db).range_samples(start, start + 3_600_000, limit=20_000)))
            self.assertLess(seconds, 2.0)
            _, seconds = timed(lambda: require(Reader(db).range_minutes(to_ms(START), to_ms(START) + 7 * 86_400_000)))
            self.assertLess(seconds, 3.0)


if __name__ == "__main__":
    unittest.main()

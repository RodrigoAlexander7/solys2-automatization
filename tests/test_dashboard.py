import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from smp22x.acquisition import Acquisition, AcquisitionConfig
from smp22x.api import create_app
from smp22x.config import Settings, settings
from smp22x.metrics import comparison
from smp22x.result import Err, Failure, Ok, require, StorageFailure
from smp22x.sensor import Sample, SMPSensor
from smp22x.storage import Reader, Writer
from smp22x.tracing import traced, trace_id, trace_path
from test_smp22x import FakeBus, fast_regs


def sample(address=3, value=812.3, offset=0):
    return Sample(address, datetime.now(timezone.utc) + timedelta(seconds=offset),
                  ok=True, mode=1, status=0, irradiance=value, scale_factor=1,
                  raw_registers={4: 1, 5: 8123})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.writer = Writer(self.path)
        require(self.writer.open())
        self.addCleanup(self.writer.close)
        require(self.writer.start("run", {"config": {"interval_s": 1}, "simulated": True}))
        self.reader = Reader(self.path)

    def insert(self, cycle=0, samples=None):
        require(self.writer.commit_cycle("run", cycle, samples or [sample()], {"cycle": cycle}))

    def test_wal_concurrent_reader_and_originals(self):
        self.assertEqual(self.writer.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        with contextlib.closing(sqlite3.connect(self.path)) as other:
            other.execute("BEGIN")
            self.assertEqual(other.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
            self.insert()
            self.assertEqual(other.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
        row = require(self.reader.samples("run"))["items"][0]
        self.assertEqual(row["raw_registers"]["5"], 8123)
        self.assertEqual(row["shortwave_wm2"], 812.3)
        self.assertIsNone(row["net_longwave_wm2"])

    def test_cycle_rollback_on_duplicate(self):
        result = self.writer.commit_cycle("run", 0, [sample(), sample()], {})
        self.assertIsInstance(result, Err)
        self.assertEqual(require(self.reader.samples("run"))["items"], [])

    def test_only_one_writer_and_lock_released(self):
        second = Writer(self.path)
        self.assertEqual(second.open().error.code, "writer_active")
        self.writer.close()
        require(second.open())
        second.close()

    def test_running_run_marked_interrupted_on_reopen(self):
        self.writer.close()
        require(self.writer.open())
        self.assertEqual(require(self.reader.run("run"))["state"], "interrupted")

    def test_cursor_and_filters(self):
        self.insert(samples=[sample(3), sample(4)])
        a = require(self.reader.samples("run", limit=1))
        b = require(self.reader.samples("run", a["next_cursor"], limit=1))
        self.assertTrue(a["has_more"])
        self.assertNotEqual(a["items"][0]["id"], b["items"][0]["id"])
        self.assertFalse(b["has_more"])
        self.assertEqual(require(self.reader.samples("run", address=5))["items"], [])

    def test_backup_contains_live_wal_data(self):
        self.insert()
        target = self.path.with_name("backup.db")
        require(self.reader.backup(target))
        self.assertEqual(len(require(Reader(target).samples("run"))["items"]), 1)
        self.assertIsInstance(self.reader.backup(target), Err)

    def test_export_matches_persisted_data(self):
        import csv
        self.insert()
        target = self.path.parent / "export"
        require(self.reader.export("run", target))
        with open(target / "raw_1s.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(float(rows[0]["irradiance_wm2"]), 812.3)
        self.assertIsInstance(self.reader.export("run", target), Err)

    def test_failed_samples_retained_and_excluded_from_means(self):
        failed = Sample(3, sample().time_utc, ok=False, error="timeout", error_code="communication_error")
        self.insert(0, [sample()])
        self.insert(1, [failed])
        minutes = require(self.reader.minutes("run"))
        self.assertEqual(sum(r["n_samples"] for r in minutes), 2)
        self.assertEqual(sum(r["n_valid"] for r in minutes), 1)
        valid_minutes = [r for r in minutes if r["n_valid"]]
        self.assertAlmostEqual(valid_minutes[0]["mean_wm2"], 812.3)

    def test_busy_is_typed(self):
        with contextlib.closing(sqlite3.connect(self.path)) as other:
            other.execute("BEGIN IMMEDIATE")
            result = self.writer.commit_cycle("run", 0, [sample()], {})
            self.assertEqual(result.error.code, "database_busy")

    def test_unknown_schema_rejected_by_reader_and_writer(self):
        self.writer.connection.execute("PRAGMA user_version=99")
        self.assertEqual(self.reader.runs().error.code, "schema_mismatch")
        self.writer.close()
        self.assertEqual(self.writer.open().error.code, "schema_mismatch")


class ApiTests(unittest.TestCase):
    insert = StoreTests.insert

    def setUp(self):
        StoreTests.setUp(self)
        self.client = TestClient(create_app(Settings(db=self.path, frontend=self.path.parent / "absent")))

    def test_empty_unknown_and_missing_database(self):
        self.assertEqual(self.client.get("/api/runs/run/samples").json()["items"], [])
        self.assertEqual(self.client.get("/api/runs/absent").status_code, 404)
        self.writer.close()
        missing = TestClient(create_app(Settings(db=self.path.parent / "missing.db")))
        self.assertEqual(missing.get("/api/runs").status_code, 503)

    def test_validation_and_trace_id(self):
        for suffix in ("?limit=999999", "?after_id=-1", "?start=2026-01-01", "?start=2026-02-01Z&end=2026-01-01Z"):
            response = self.client.get("/api/runs/run/samples" + suffix)
            self.assertEqual(response.status_code, 422)
            self.assertFalse(response.json()["ok"])
            self.assertEqual(response.json()["trace_id"], response.headers["X-Trace-ID"])

    def test_only_read_routes(self):
        self.assertEqual(self.client.post("/api/runs").status_code, 405)
        self.insert()
        self.assertEqual(len(self.client.get("/api/runs/run/samples").json()["items"]), 1)

    def test_sse_cursor_reconnect(self):
        self.insert(samples=[sample(3), sample(4)])
        page = self.client.get("/api/runs/run/samples?limit=1").json()
        response = self.client.get("/api/runs/run/stream?once=true", headers={"Last-Event-ID": str(page["next_cursor"])})
        self.assertEqual(response.status_code, 200)
        data = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["address"], 4)
        self.assertEqual(self.client.get("/api/runs/run/stream?once=true", headers={"Last-Event-ID": "bad"}).status_code, 422)

    def test_stale_is_distinct_from_invalid(self):
        self.insert(samples=[sample(offset=-30)])
        status = self.client.get("/api/runs/run/status").json()
        self.assertTrue(status["latest"][0]["stale"])
        self.assertTrue(status["latest"][0]["valid"])

    def test_acquisition_persists_when_web_is_absent(self):
        cfg = AcquisitionConfig(addresses=[3], db_path=self.path.parent / "independent.db",
                                interval_s=0.02, duration_s=0.06, out_dir=self.path.parent / "out",
                                live=False, clear_errors=False)
        with contextlib.redirect_stdout(io.StringIO()):
            acq = Acquisition(FakeBus(fast_regs()), cfg)
            acq.run()
        self.assertTrue(require(Reader(cfg.db_path).samples(acq.run_id))["items"])
        self.assertEqual(require(Reader(cfg.db_path).run(acq.run_id))["state"], "duration")


class ErrorTests(unittest.TestCase):
    def test_invalid_register_block(self):
        bus = FakeBus(fast_regs())
        with patch.object(bus, "read_input_registers", return_value=[0]):
            result = SMPSensor(bus, 3).read_sample()
            self.assertEqual(result.error_code, "invalid_register_block")

    def test_low_light_and_unpaired_comparison(self):
        self.assertIsNone(comparison([])["difference_pct"])
        from smp22x.storage import sample_record
        rows = [sample_record(sample(3, 0), 1), sample_record(sample(4, 0), 1)]
        self.assertIsNone(comparison(rows)["difference_pct"])
        rows[1]["cycle"] = 2
        self.assertIsNone(comparison(rows)["difference_wm2"])

    def test_trace_chain_and_typed_result(self):
        @traced("Adapter", fields=("run_id",))
        def child(run_id):
            return Err(Failure("database_busy", "busy", True))
        @traced("UseCase")
        def parent():
            return child("test")
        with self.assertLogs("smp22x.trace", level="INFO") as captured:
            self.assertIsInstance(parent(), Err)
        entries = [json.loads(line.split(":", 2)[2]) for line in captured.output]
        self.assertEqual(entries[0]["path"], "UseCase -> Adapter")
        self.assertEqual(entries[0]["run_id"], "test")
        self.assertEqual(entries[0]["trace_id"], entries[1]["trace_id"])
        self.assertEqual(trace_path.get(), ())
        self.assertEqual(trace_id.get(), "")

    def test_bad_settings(self):
        for env in ({"SMP_API_PORT": "0"}, {"SMP_POLL_S": "nan"}, {"SMP_STALE_S": "0"}):
            with self.assertRaises(ValueError):
                settings(env)

    def test_storage_failure_stops_acquisition(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = AcquisitionConfig(addresses=[3], interval_s=0.01, duration_s=0.02,
                                    out_dir=Path(directory), live=False, clear_errors=False)
            with patch.object(Writer, "commit_cycle", return_value=Err(Failure("storage_failure", "disk full"))):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(StorageFailure):
                    acq = Acquisition(FakeBus(fast_regs()), cfg)
                    acq.run()
            self.assertEqual(require(Reader(acq.run_dir / "run.sqlite3").run(acq.run_id))["state"], "error")


if __name__ == "__main__":
    unittest.main()

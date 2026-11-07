"""Almacenamiento (R11–R13, R24; C09–C11, C15): esquema tipado, migración, minutos, eventos y archivo."""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pyarrow.parquet as pq

from smp22x import acquisition
from smp22x import registers as reg
from smp22x.acquisition import Acquisition, AcquisitionConfig
from smp22x.archive import day_path, day_range, run_once, write_day
from smp22x.events import EventTracker, to_ms
from smp22x.history import range_samples
from smp22x.result import require
from smp22x.sensor import Sample
from smp22x.storage import REGISTER_COLUMNS, SCHEMA_VERSION, VALUE_FIELDS, Reader, Writer, migrate
from scenario import SUMMER_DAY, simulate
from test_smp22x import FakeBus, fast_regs, sgr_regs

T0 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def sample(address=3, value=812.3, t=T0, status=0, **extra) -> Sample:
    return Sample(address, t, ok=True, mode=1, status=status, irradiance=value, scale_factor=1,
                  body_temp_c=25.0, raw_registers={2: 1, 3: status, 4: 1, 5: round(value * 10) & 0xFFFF}, **extra)


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "acq.sqlite3"


class SchemaTests(TempDir):
    def test_typed_columns_and_no_json_payload(self):
        writer = Writer(self.db)
        require(writer.open())
        self.addCleanup(writer.close)
        columns = {r[1] for r in writer.connection.execute("PRAGMA table_info(samples)")}
        self.assertNotIn("payload", columns)
        self.assertTrue(set(VALUE_FIELDS) | set(REGISTER_COLUMNS) <= columns)
        self.assertEqual(writer.connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_original_words_round_trip_and_derived_on_read(self):
        writer = Writer(self.db)
        require(writer.open())
        self.addCleanup(writer.close)
        require(writer.start("r", {"config": {"interval_s": 1}}))
        sgr = Sample(2, T0, ok=True, sensor_type=reg.PYRGEOMETER, mode=1, status=0, scale_factor=1,
                     irradiance=-130.2, lw_down=319.7, body_temp_c=25.3,
                     raw_registers={5: -1302 & 0xFFFF, 10: 3197, 16: 48, 17: 10125})
        require(writer.commit_cycle("r", 0, [sgr], {}))
        row = require(Reader(self.db).samples("r"))["items"][0]
        self.assertEqual(row["raw_registers"], {"5": 64234, "10": 3197, "16": 48, "17": 10125})
        self.assertEqual(row["measurement"], 319.7)
        self.assertEqual(row["net_longwave_wm2"], -130.2)
        self.assertIsNone(row["shortwave_wm2"])
        self.assertAlmostEqual(row["lw_down_calc"], -130.2 + 5.67e-8 * (25.3 + 273.15) ** 4)
        self.assertEqual(row["time_utc"], "2026-06-21T12:00:00.000+00:00")
        self.assertEqual(row["conversion_version"], "kz-smart-v1")

    def test_bytes_per_row_is_bounded(self):
        """Decisión de volumen: muy por debajo de los ~1,1 KB/fila del esquema 1."""
        simulate(self.db, hours=6, step_s=10)
        rows = 6 * 360 * 3
        self.assertLess(self.db.stat().st_size / rows, 320)

    def test_schema1_database_is_refused_with_migration_hint(self):
        legacy_db(self.db)
        result = Writer(self.db).open()
        self.assertEqual(result.error.code, "schema_mismatch")
        self.assertIn("migrate", result.error.message)
        self.assertIn("migrate", Reader(self.db).runs().error.message)

    def test_range_query_crosses_runs_in_time_order(self):
        writer = Writer(self.db)
        require(writer.open())
        self.addCleanup(writer.close)
        for run, offset in (("a", 0), ("b", 30)):
            require(writer.start(run, {}))
            for i in range(3):
                require(writer.commit_cycle(run, i, [sample(t=T0 + timedelta(seconds=offset + i))], {}))
        page = require(Reader(self.db).range_samples(to_ms(T0), to_ms(T0) + 60_000, limit=4))
        self.assertEqual(len(page["items"]), 4)
        rest = require(Reader(self.db).range_samples(to_ms(T0), to_ms(T0) + 60_000, after=page["next"]))
        times = [r["t_ms"] for r in page["items"] + rest["items"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), 6)
        self.assertEqual({r["run_id"] for r in page["items"] + rest["items"]}, {"a", "b"})


def legacy_db(path: Path, samples: list[tuple[str, int, dict]] | None = None) -> None:
    """Base con el esquema 1 original (JSON por muestra)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE runs (id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, ended_utc TEXT, state TEXT NOT NULL,
     metadata TEXT NOT NULL, status TEXT NOT NULL);
    CREATE TABLE samples (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
     cycle INTEGER NOT NULL, address INTEGER NOT NULL, time_utc TEXT NOT NULL, valid INTEGER NOT NULL,
     measurement REAL, payload TEXT NOT NULL, UNIQUE(run_id, cycle, address));
    PRAGMA user_version=1;""")
    conn.execute("INSERT INTO runs VALUES('old','2026-06-21T12:00:00.000+00:00',NULL,'interrupted',?,'{}')",
                 (json.dumps({"config": {"interval_s": 1, "addresses": [2, 3]}}),))
    for time_utc, cycle, payload in samples or []:
        conn.execute("INSERT INTO samples(run_id,cycle,address,time_utc,valid,measurement,payload) VALUES('old',?,?,?,?,?,?)",
                     (cycle, payload["address"], time_utc, int(payload["valid"]), payload.get("measurement"), json.dumps(payload)))
    conn.commit()
    conn.close()


def legacy_payload(address: int, t: datetime, cycle: int, ok=True, valid=True, value=812.3, pyrg=False) -> dict:
    return {"address": address, "time_utc": t.isoformat(timespec="milliseconds"), "ok": ok,
            "error": "" if ok else "timeout", "sensor_type": "pyrgeometer" if pyrg else "pyranometer",
            "mode": 1 if ok else None, "status": 0 if ok else None, "scale_factor": 1 if ok else None,
            "irradiance": (-120.5 if pyrg else value) if ok else None, "raw_irradiance": None,
            "lw_down": 320.1 if pyrg and ok else None, "raw_lw_down": None, "stdev": 0.1 if ok else None,
            "body_temp_c": 24.5 if ok else None, "supply_v": 12.0 if ok else None, "rh_pct": None,
            "pressure_hpa": None, "rtt_ms": 21.5 if ok else None,
            "error_code": None if ok else "communication_error",
            "raw_registers": {"2": 1, "3": 0, "4": 1, "5": 8123} if ok else {}, "cycle": cycle, "valid": valid,
            "measurement": (320.1 if pyrg else value) if ok else None}


class MigrationTests(TempDir):
    def rows(self):
        out = []
        for i in range(130):  # cruza dos minutos completos y uno parcial
            t = T0 + timedelta(seconds=i)
            out.append((t.isoformat(timespec="milliseconds"), i, legacy_payload(2, t, i, pyrg=True)))
            ok = i % 17 != 0
            out.append((t.isoformat(timespec="milliseconds"), i, legacy_payload(3, t, i, ok=ok, valid=ok, value=800 + i)))
        return out

    def test_migration_is_lossless_and_keeps_backup(self):
        legacy_db(self.db, self.rows())
        report = require(migrate(self.db))
        self.assertEqual(report["samples"], 260)
        backup = Path(report["backup"])
        self.assertTrue(backup.exists())
        with contextlib.closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM samples").fetchone()[0], 260)
        reader = Reader(self.db)
        items = require(reader.samples("old", limit=5000))["items"]
        self.assertEqual(len(items), 260)
        failed = [r for r in items if not r["ok"]]
        self.assertTrue(failed)
        self.assertTrue(all(r["error_code"] == "communication_error" and r["measurement"] is None for r in failed))
        self.assertEqual(require(reader.run("old"))["state"], "interrupted")

    def test_minutes_are_rebuilt_from_valid_samples(self):
        legacy_db(self.db, self.rows())
        require(migrate(self.db))
        minutes = {(m["address"], m["minute_utc"]): m for m in require(Reader(self.db).minutes("old", 100))}
        first = minutes[(3, "2026-06-21T12:00:00+00:00")]
        valid = [800 + i for i in range(60) if i % 17 != 0]
        self.assertEqual(first["n_samples"], 60)
        self.assertEqual(first["n_valid"], len(valid))
        self.assertAlmostEqual(first["mean_wm2"], sum(valid) / len(valid))
        self.assertFalse(minutes[(3, "2026-06-21T12:02:00+00:00")]["complete"])  # minuto parcial

    def test_second_migration_does_nothing(self):
        legacy_db(self.db, self.rows()[:4])
        require(migrate(self.db))
        self.assertEqual(require(migrate(self.db))["status"], "already_current")

    def test_empty_database_and_writer_lock(self):
        legacy_db(self.db)
        writer_lock = Writer(self.dir / "other.sqlite3")
        require(writer_lock.open())
        writer_lock.close()
        self.assertEqual(require(migrate(self.db))["samples"], 0)
        legacy = self.dir / "busy.sqlite3"
        legacy_db(legacy)
        from smp22x.storage import WriterLock
        lock = WriterLock(legacy)
        self.assertTrue(lock.acquire())
        try:
            self.assertEqual(migrate(legacy).error.code, "writer_active")
        finally:
            lock.release()

    def test_unknown_schema_is_not_touched(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE x(a)")
        conn.execute("PRAGMA user_version=7")
        conn.commit()
        conn.close()
        self.assertEqual(migrate(self.db).error.code, "schema_mismatch")


class MinuteAndEventPersistenceTests(TempDir):
    def test_acquisition_persists_minutes_equal_to_csv(self):
        cfg = AcquisitionConfig(addresses=[2, 3], pyrgeometers=[2], interval_s=0.02, duration_s=0.3,
                                out_dir=self.dir / "out", live=False, db_path=self.db, clear_errors=False)
        bus = FakeBus(fast_regs())
        bus.regs = {**sgr_regs()}
        with contextlib.redirect_stdout(io.StringIO()):
            acq = Acquisition(bus, cfg)
            run_dir = acq.run()
        stored = require(Reader(self.db).minutes(acq.run_id, 100))
        csv_rows = (run_dir / "minute_avg.csv").read_text(encoding="utf-8").strip().splitlines()[1:]
        self.assertEqual(len(stored), len(csv_rows))
        self.assertEqual(sum(m["n_samples"] for m in stored),
                         len(require(Reader(self.db).samples(acq.run_id, limit=5000))["items"]))

    def test_run_lifecycle_events(self):
        cfg = AcquisitionConfig(addresses=[3], interval_s=0.02, duration_s=0.1, out_dir=self.dir / "out",
                                live=False, db_path=self.db)
        with contextlib.redirect_stdout(io.StringIO()):
            Acquisition(FakeBus(fast_regs()), cfg).run()
        kinds = [e["kind"] for e in require(Reader(self.db).events(0, 2 ** 62))]
        self.assertIn("run_started", kinds)
        self.assertIn("run_finished", kinds)

    def test_port_loss_events_are_committed(self):
        cfg = AcquisitionConfig(addresses=[2, 3], interval_s=0.05, duration_s=0.4, out_dir=self.dir / "out",
                                live=False, db_path=self.db, clear_errors=False)
        with mock.patch.object(acquisition, "PORT_RETRY_S", 0.0), contextlib.redirect_stdout(io.StringIO()):
            Acquisition(FakeBus(fast_regs(), port_error_once_for=3), cfg).run()
        events = require(Reader(self.db).events(0, 2 ** 62))
        lost = [e for e in events if e["kind"] == "port_lost"]
        recovered = [e for e in events if e["kind"] == "port_recovered"]
        self.assertEqual(len(lost), 1)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(lost[0]["severity"], "error")

    def test_interrupted_run_leaves_an_event(self):
        writer = Writer(self.db)
        require(writer.open())
        require(writer.start("crashed", {}))
        writer.close()  # simula un cierre sin finish
        writer = Writer(self.db)
        require(writer.open())
        writer.close()
        kinds = [e["kind"] for e in require(Reader(self.db).events(0, 2 ** 62))]
        self.assertIn("run_interrupted", kinds)


class EventTrackerTests(unittest.TestCase):
    def observe(self, tracker, statuses, address=3, step=1):
        events = []
        for i, status in enumerate(statuses):
            events += tracker.observe(sample(address, t=T0 + timedelta(seconds=i * step), status=status))
        return events

    def test_startup_flags_then_cleared(self):
        events = self.observe(EventTracker(), [0x0108] + [0] * 12)
        self.assertEqual([e.kind for e in events], ["flags_changed", "flags_changed"])
        self.assertEqual(events[0].detail["set"], ["ERROR", "POWER_FAILED"])
        self.assertEqual(events[1].detail["cleared"], ["ERROR", "POWER_FAILED"])
        self.assertEqual(events[1].severity, "info")

    def test_no_event_for_a_clean_start(self):
        self.assertEqual(self.observe(EventTracker(), [0] * 30), [])

    def test_flapping_is_rate_limited_and_converges(self):
        statuses = [1 if i % 2 else 0 for i in range(120)] + [1] * 15
        events = self.observe(EventTracker(), statuses)
        self.assertLessEqual(len(events), 14)  # ≤ 1 cada 10 s durante ~135 s
        changed = [e for e in events if e.kind == "flags_changed"]
        self.assertEqual(changed[-1].detail["status"], 1)  # el último evento refleja el estado final
        self.assertTrue(any(e.detail.get("suppressed") or e.kind == "flags_flapping" for e in events))
        self.assertEqual(changed[-1].severity, "error")  # VOID_DATA invalida

    def test_desiccant_is_warning_not_error(self):
        events = self.observe(EventTracker(), [1 << 11])
        self.assertEqual(events[0].severity, "warning")
        self.assertEqual(events[0].detail["names"], ["DESICCANT_WARNING"])

    def test_silent_and_recovered(self):
        tracker = EventTracker()
        events = []
        for i in range(7):
            events += tracker.observe(Sample(3, T0 + timedelta(seconds=i), ok=False, error="timeout"))
        events += tracker.observe(sample(t=T0 + timedelta(seconds=8)))
        self.assertEqual([e.kind for e in events], ["sensor_silent", "sensor_recovered"])
        self.assertEqual(events[1].detail["after_failures"], 7)

    def test_mode_change(self):
        tracker = EventTracker()
        tracker.observe(sample())
        s = sample(t=T0 + timedelta(seconds=1))
        s.mode = 2
        events = tracker.observe(s)
        self.assertEqual(events[0].kind, "mode_changed")
        self.assertEqual(events[0].detail["mode_name"], "service")


AFTER_RETENTION = SUMMER_DAY + timedelta(days=31, hours=3)  # día 1 vencido (30 días), día 2 todavía no


class ArchiveTests(TempDir):
    def setUp(self):
        super().setUp()
        self.root = self.dir / "archive"
        simulate(self.db, start=SUMMER_DAY, hours=48, step_s=600)  # dos días UTC
        self.day1 = SUMMER_DAY.date().isoformat()
        self.day2 = (SUMMER_DAY + timedelta(days=1)).date().isoformat()

    def test_write_day_contains_all_rows_and_metadata(self):
        archived = require(write_day(self.db, self.root, self.day1))
        table = pq.read_table(archived.path)
        start, end = day_range(self.day1)
        self.assertEqual(table.num_rows, require(Reader(self.db).count_range(start, end)))
        meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
        self.assertEqual(meta["schema_version"], "2")
        self.assertEqual(json.loads(meta["conversion_versions"]), ["kz-smart-v1"])
        self.assertIn("reg_5", table.column_names)
        self.assertEqual(str(table.schema.field("time_utc").type), "timestamp[ms, tz=UTC]")

    def test_retention_prunes_only_verified_expired_days(self):
        now = to_ms(AFTER_RETENTION)
        report = require(run_once(self.db, self.root, retention_days=30, now_ms=now))
        self.assertEqual(sorted(report["archived"]), [self.day1, self.day2])
        self.assertEqual(report["pruned"], [self.day1])  # el día 2 todavía no vence
        start, end = day_range(self.day1)
        reader = Reader(self.db)
        self.assertEqual(require(reader.count_range(start, end)), 0)
        self.assertGreater(len(require(reader.range_minutes(start, end))["items"]), 0)  # minutos se conservan
        manifest = require(reader.manifest())
        self.assertIsNotNone(manifest[self.day1]["pruned_ms"])
        self.assertIsNone(manifest[self.day2]["pruned_ms"])

    def test_query_across_active_and_archive_without_duplicates(self):
        before = require(range_samples(Reader(self.db), self.root, to_ms(SUMMER_DAY), to_ms(SUMMER_DAY + timedelta(days=2))))
        require(run_once(self.db, self.root, retention_days=30, now_ms=to_ms(AFTER_RETENTION)))
        after = require(range_samples(Reader(self.db), self.root, to_ms(SUMMER_DAY), to_ms(SUMMER_DAY + timedelta(days=2))))
        self.assertEqual([r["id"] for r in before["items"]], [r["id"] for r in after["items"]])
        self.assertEqual([r["measurement"] for r in before["items"]], [r["measurement"] for r in after["items"]])
        self.assertEqual(before["items"][5]["raw_registers"], after["items"][5]["raw_registers"])

    def test_altered_archive_blocks_pruning(self):
        now = to_ms(AFTER_RETENTION)
        require(run_once(self.db, self.root, retention_days=0, now_ms=now))  # solo archiva
        path = day_path(self.root, self.day1)
        path.write_bytes(path.read_bytes() + b"tampered")
        report = require(run_once(self.db, self.root, retention_days=30, now_ms=now))
        self.assertEqual(report["pruned"], [])
        self.assertTrue(any("SHA-256" in f["error"] for f in report["failed"]))
        start, end = day_range(self.day1)
        self.assertGreater(require(Reader(self.db).count_range(start, end)), 0)

    def test_late_rows_after_pruning_are_merged(self):
        """C15: reloj corregido hacia atrás después de podar un día."""
        now = to_ms(AFTER_RETENTION)
        require(run_once(self.db, self.root, retention_days=30, now_ms=now))
        rows_before = pq.read_table(day_path(self.root, self.day1)).num_rows
        writer = Writer(self.db)
        require(writer.open())
        require(writer.start("tardia", {}))
        require(writer.commit_cycle("tardia", 0, [sample(t=SUMMER_DAY + timedelta(hours=5))], {}))
        writer.close()
        report = require(run_once(self.db, self.root, retention_days=30, now_ms=now))
        self.assertIn(self.day1, report["archived"])
        self.assertIn(self.day1, report["pruned"])
        self.assertEqual(pq.read_table(day_path(self.root, self.day1)).num_rows, rows_before + 1)

    def test_open_day_is_not_archived(self):
        report = require(run_once(self.db, self.root, retention_days=30,
                                  now_ms=to_ms(SUMMER_DAY + timedelta(days=1, minutes=30))))
        self.assertEqual(report["archived"], [])  # el día 1 cerró hace menos de 1 h

    def test_archive_requires_writer_lock(self):
        writer = Writer(self.db)
        require(writer.open())
        try:
            self.assertEqual(run_once(self.db, self.root, 30).error.code, "writer_active")
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()

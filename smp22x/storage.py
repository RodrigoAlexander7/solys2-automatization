"""SQLite acquisition store. One OS-locked writer, short read-only snapshots."""
from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Callable, TypeVar

from .result import Err, Failure, Ok, Result
from .sensor import Sample
from .tracing import traced

SCHEMA_VERSION = 1
CONVERSION_VERSION = "kz-smart-v1"
T = TypeVar("T")
SCHEMA = """
CREATE TABLE runs (
 id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, ended_utc TEXT,
 state TEXT NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE samples (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES runs(id), cycle INTEGER NOT NULL,
 address INTEGER NOT NULL, time_utc TEXT NOT NULL, valid INTEGER NOT NULL,
 measurement REAL, payload TEXT NOT NULL,
 UNIQUE(run_id, cycle, address)
);
CREATE INDEX sample_history ON samples(run_id,address,time_utc);
CREATE INDEX sample_cursor ON samples(run_id,id);
PRAGMA user_version=1;
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def database_error(exc: sqlite3.Error) -> Err:
    busy = getattr(exc, "sqlite_errorcode", 0) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return Err(Failure("database_busy" if busy else "storage_failure", str(exc), busy))


def sample_record(s: Sample, cycle: int) -> dict:
    record = asdict(s)
    record["time_utc"] = s.time_utc.isoformat(timespec="milliseconds")
    record.update(cycle=cycle, valid=s.valid, measurement=s.measurement,
                  shortwave_wm2=s.irradiance if s.sensor_type == "pyranometer" else None,
                  net_longwave_wm2=s.irradiance if s.sensor_type == "pyrgeometer" else None,
                  lw_down_calc=s.lw_down_calc, conversion_version=CONVERSION_VERSION)
    record["lw_check_delta"] = (
        s.lw_down - s.lw_down_calc if s.lw_down is not None and s.lw_down_calc is not None else None
    )
    return record


class Writer:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.connection: sqlite3.Connection | None = None
        self.lock = None

    @traced("SQLiteWriter.open")
    def open(self) -> Result[None]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock = open(str(self.path) + ".writer.lock", "a+b")
            try:
                if __import__("os").name == "nt":
                    import msvcrt
                    self.lock.seek(0)
                    if not self.lock.read(1):
                        self.lock.write(b"0")
                        self.lock.flush()
                    self.lock.seek(0)
                    msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.lock.close()
                self.lock = None
                return Err(Failure("writer_active", "Ya hay una adquisición escribiendo esta BD"))
            self.connection = sqlite3.connect(self.path, timeout=0.5)
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version != SCHEMA_VERSION and (version != 0 or tables):
                self.close()
                return Err(Failure("schema_mismatch", f"Esquema {version}; se requiere {SCHEMA_VERSION}"))
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            if not tables:
                self.connection.executescript(SCHEMA)
            with self.connection:
                self.connection.execute(
                    "UPDATE runs SET state='interrupted',ended_utc=? WHERE state='running'", (now_utc(),))
            return Ok(None)
        except (sqlite3.Error, OSError) as exc:
            self.close()
            return database_error(exc) if isinstance(exc, sqlite3.Error) else Err(Failure("storage_failure", str(exc)))

    @traced("SQLiteWriter.start", fields=("run_id",))
    def start(self, run_id: str, metadata: dict) -> Result[None]:
        try:
            assert self.connection is not None
            with self.connection:
                self.connection.execute("INSERT INTO runs VALUES(?,?,NULL,'running',?,?)",
                                        (run_id, now_utc(), json.dumps(metadata), json.dumps({"heartbeat_utc": now_utc()})))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.commit_cycle", fields=("run_id", "cycle"))
    def commit_cycle(self, run_id: str, cycle: int, samples: list[Sample], status: dict) -> Result[None]:
        try:
            assert self.connection is not None
            with self.connection:
                for s in samples:
                    self.connection.execute(
                        "INSERT INTO samples(run_id,cycle,address,time_utc,valid,measurement,payload) VALUES(?,?,?,?,?,?,?)",
                        (run_id, cycle, s.address, s.time_utc.isoformat(timespec="milliseconds"),
                         int(s.valid), s.measurement, json.dumps(sample_record(s, cycle), allow_nan=False)))
                self.connection.execute("UPDATE runs SET status=? WHERE id=?",
                                        (json.dumps({**status, "heartbeat_utc": now_utc()}), run_id))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.finish", fields=("run_id",))
    def finish(self, run_id: str, reason: str, metadata: dict) -> Result[None]:
        try:
            assert self.connection is not None
            with self.connection:
                self.connection.execute("UPDATE runs SET ended_utc=?,state=?,metadata=? WHERE id=?",
                                        (now_utc(), reason, json.dumps(metadata), run_id))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.metadata", fields=("run_id",))
    def metadata(self, run_id: str, metadata: dict) -> Result[None]:
        try:
            with self.connection:
                self.connection.execute("UPDATE runs SET metadata=? WHERE id=?", (json.dumps(metadata), run_id))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.lock is not None:
            # Closing releases OS locks, also after abrupt process exit.
            self.lock.close()
            self.lock = None


class Reader:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def query(self, action: Callable[[sqlite3.Connection], T]) -> Result[T]:
        if not self.path.exists():
            return Err(Failure("database_unavailable", "La adquisición todavía no ha creado la BD", True))
        try:
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.5) as conn:
                conn.row_factory = sqlite3.Row
                if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    return Err(Failure("schema_mismatch", "Versión de esquema incompatible"))
                return Ok(action(conn))
        except sqlite3.Error as exc:
            return database_error(exc)
        finally:
            # sqlite3 context manager ends transactions, not connection lifetime.
            if "conn" in locals():
                conn.close()

    @traced("SQLiteReader.runs")
    def runs(self, limit: int = 100) -> Result[list[dict]]:
        return self.query(lambda c: [self._run(r) for r in c.execute(
            "SELECT * FROM runs ORDER BY started_utc DESC LIMIT ?", (limit,))])

    @staticmethod
    def _run(row) -> dict:
        return {**dict(row), "metadata": json.loads(row["metadata"]), "status": json.loads(row["status"])}

    @traced("SQLiteReader.run", fields=("run_id",))
    def run(self, run_id: str) -> Result[dict]:
        result = self.query(lambda c: c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        if isinstance(result, Err):
            return result
        if result.value is None:
            return Err(Failure("run_not_found", "Corrida no encontrada"))
        return Ok(self._run(result.value))

    @traced("SQLiteReader.samples", fields=("run_id", "after_id"))
    def samples(self, run_id: str, after_id: int = 0, limit: int = 1000,
                address: int | None = None, start: str | None = None, end: str | None = None,
                recent: bool = False) -> Result[dict]:
        def read(c):
            sql = "SELECT id,payload FROM samples WHERE run_id=? AND id>?"
            params: list = [run_id, after_id]
            for name, op, value in (("address", "=", address), ("time_utc", ">=", start), ("time_utc", "<=", end)):
                if value is not None:
                    sql += f" AND {name}{op}?"
                    params.append(value)
            rows = c.execute(sql + (" ORDER BY id DESC LIMIT ?" if recent else " ORDER BY id LIMIT ?"),
                             (*params, limit + 1)).fetchall()
            chosen = list(reversed(rows[:limit])) if recent else rows[:limit]
            items = [{**json.loads(r["payload"]), "id": r["id"]} for r in chosen]
            return {"items": items, "next_cursor": items[-1]["id"] if items else after_id,
                    "has_more": not recent and len(rows) > limit,
                    "history_truncated": recent and len(rows) > limit}
        return self.query(read)

    @traced("SQLiteReader.latest", fields=("run_id",))
    def latest(self, run_id: str) -> Result[list[dict]]:
        return self.query(lambda c: [dict(id=r["id"], **json.loads(r["payload"])) for r in c.execute(
            "SELECT id,payload FROM samples WHERE id IN (SELECT MAX(id) FROM samples WHERE run_id=? GROUP BY address)",
            (run_id,))])

    @traced("SQLiteReader.minutes", fields=("run_id",))
    def minutes(self, run_id: str, limit: int = 1000) -> Result[list[dict]]:
        def read(c):
            rows = c.execute("""SELECT substr(time_utc,1,16) AS minute_utc,address,
                COUNT(*) AS n_samples,SUM(valid) AS n_valid,
                AVG(CASE WHEN valid THEN measurement END) AS mean_wm2,
                MIN(CASE WHEN valid THEN measurement END) AS min_wm2,
                MAX(CASE WHEN valid THEN measurement END) AS max_wm2,
                AVG(CASE WHEN valid THEN measurement*measurement END) AS mean_square
                FROM samples WHERE run_id=? GROUP BY substr(time_utc,1,16),address
                ORDER BY minute_utc DESC,address LIMIT ?""", (run_id, limit)).fetchall()
            result = []
            for row in rows:
                r = dict(row)
                r["minute_utc"] += ":00+00:00"
                r["std_wm2"] = math.sqrt(max(0, r.pop("mean_square") - r["mean_wm2"] ** 2)) if r["n_valid"] > 1 else None
                r.pop("mean_square", None)
                result.append(r)
            return result
        return self.query(read)

    @traced("SQLiteReader.backup")
    def backup(self, destination: Path) -> Result[None]:
        if destination.resolve() == self.path:
            return Err(Failure("invalid_input", "El backup debe tener otra ruta"))
        if destination.exists():
            return Err(Failure("invalid_input", "El destino de backup ya existe"))
        def copy(source):
            target = sqlite3.connect(destination)
            try:
                source.backup(target, pages=256)
            finally:
                target.close()
        return self.query(copy)

    @traced("SQLiteReader.export", fields=("run_id",))
    def export(self, run_id: str, destination: Path) -> Result[None]:
        from .acquisition import Acquisition, RAW_COLUMNS, MINUTE_COLUMNS, _MinuteAggregator
        found = self.run(run_id)
        if isinstance(found, Err):
            return found
        if destination.exists():
            return Err(Failure("invalid_input", "La carpeta de exportación ya existe"))
        try:
            destination.mkdir(parents=True)
            with open(destination / "raw_1s.csv", "w", newline="", encoding="utf-8") as raw, \
                    open(destination / "minute_avg.csv", "w", newline="", encoding="utf-8") as minute:
                rw, mw = csv.writer(raw), csv.writer(minute)
                rw.writerow(RAW_COLUMNS)
                mw.writerow(MINUTE_COLUMNS)
                agg = _MinuteAggregator(mw, found.value["metadata"]["config"]["interval_s"])
                cursor = 0
                # Bound snapshot at export start; active acquisition may continue.
                high = self.query(lambda c: c.execute("SELECT COALESCE(MAX(id),0) FROM samples WHERE run_id=?", (run_id,)).fetchone()[0])
                if isinstance(high, Err):
                    return high
                while cursor < high.value:
                    page = self.samples(run_id, cursor, 1000)
                    if isinstance(page, Err):
                        return page
                    if not page.value["items"]:
                        break
                    for record in page.value["items"]:
                        if record["id"] > high.value:
                            break
                        values = {k: record[k] for k in Sample.__dataclass_fields__ if k in record}
                        values["time_utc"] = datetime.fromisoformat(values["time_utc"])
                        s = Sample(**values)
                        rw.writerow(Acquisition._row(None, record["cycle"], s))
                        agg.add(s)
                    cursor = page.value["next_cursor"]
                agg.flush()
            meta = {**found.value["metadata"], "export_max_id": high.value}
            (destination / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return Ok(None)
        except OSError as exc:
            return Err(Failure("storage_failure", str(exc)))

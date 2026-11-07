"""SQLite acquisition store, schema 2. One OS-locked writer, short read-only snapshots.

Schema 2 stores one typed column per physical value and per original Modbus word,
instead of a JSON document per sample. Derived values (measurement, σT⁴ check)
are computed on read and are never stored as if they were measurements.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from . import registers as reg
from .events import Event, iso_ms, now_ms, to_ms
from .minutes import MINUTE_COLUMNS, MinuteAggregator, MinuteRecord
from .result import Err, Failure, Ok, Result
from .sensor import Sample, blackbody_wm2
from .tracing import traced

SCHEMA_VERSION = 2
CONVERSION_VERSION = "kz-smart-v1"
T = TypeVar("T")

REGISTER_ADDRESSES = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17)
REGISTER_COLUMNS = tuple(f"reg_{n}" for n in REGISTER_ADDRESSES)
VALUE_FIELDS = ("mode", "status", "scale_factor", "irradiance", "raw_irradiance", "lw_down",
                "raw_lw_down", "stdev", "body_temp_c", "supply_v", "rh_pct", "pressure_hpa", "rtt_ms")
SAMPLE_COLUMNS = ("run", "cycle", "address", "t_ms", "sensor_type", "ok", "valid", "error",
                  "error_code", *VALUE_FIELDS, *REGISTER_COLUMNS)
MINUTE_FIELDS = ("run", "address", "minute_ms", "sensor_type", "n_samples", "n_valid", "mean",
                 "min", "max", "std", "net_mean", "body_temp_mean", "complete")

SCHEMA = f"""
CREATE TABLE runs (
 pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 started_utc TEXT NOT NULL, ended_utc TEXT, state TEXT NOT NULL,
 metadata TEXT NOT NULL, status TEXT NOT NULL, conversion_version TEXT NOT NULL
);
CREATE TABLE samples (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run INTEGER NOT NULL REFERENCES runs(pk), cycle INTEGER NOT NULL,
 address INTEGER NOT NULL, t_ms INTEGER NOT NULL, sensor_type TEXT NOT NULL,
 ok INTEGER NOT NULL, valid INTEGER NOT NULL, error TEXT NOT NULL, error_code TEXT,
 mode INTEGER, status INTEGER, scale_factor INTEGER,
 irradiance REAL, raw_irradiance REAL, lw_down REAL, raw_lw_down REAL, stdev REAL,
 body_temp_c REAL, supply_v REAL, rh_pct REAL, pressure_hpa REAL, rtt_ms REAL,
 {", ".join(f"{c} INTEGER" for c in REGISTER_COLUMNS)},
 UNIQUE(run, cycle, address)
);
CREATE INDEX sample_cursor ON samples(run, id);
CREATE INDEX sample_time ON samples(t_ms);
CREATE TABLE minutes (
 run INTEGER NOT NULL REFERENCES runs(pk), address INTEGER NOT NULL, minute_ms INTEGER NOT NULL,
 sensor_type TEXT NOT NULL, n_samples INTEGER NOT NULL, n_valid INTEGER NOT NULL,
 mean REAL, min REAL, max REAL, std REAL, net_mean REAL, body_temp_mean REAL,
 complete INTEGER NOT NULL, PRIMARY KEY(run, address, minute_ms)
) WITHOUT ROWID;
CREATE INDEX minute_time ON minutes(minute_ms);
CREATE TABLE events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run INTEGER REFERENCES runs(pk),
 t_ms INTEGER NOT NULL, address INTEGER, kind TEXT NOT NULL, severity TEXT NOT NULL,
 detail TEXT NOT NULL
);
CREATE INDEX event_time ON events(t_ms);
CREATE TABLE archive_days (
 day TEXT PRIMARY KEY, path TEXT NOT NULL, rows INTEGER NOT NULL, sha256 TEXT NOT NULL,
 archived_ms INTEGER NOT NULL, pruned_ms INTEGER
);
PRAGMA user_version={SCHEMA_VERSION};
"""

SAMPLE_SELECT = "SELECT s.*, r.id AS run_id, r.conversion_version FROM samples s JOIN runs r ON r.pk = s.run"
MINUTE_SELECT = "SELECT m.*, r.id AS run_id FROM minutes m JOIN runs r ON r.pk = m.run"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def database_error(exc: sqlite3.Error) -> Err:
    busy = getattr(exc, "sqlite_errorcode", 0) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return Err(Failure("database_busy" if busy else "storage_failure", str(exc), busy))


def sample_params(s: Sample, run_pk: int, cycle: int) -> tuple:
    regs = s.raw_registers or {}
    return (run_pk, cycle, s.address, to_ms(s.time_utc), s.sensor_type, int(s.ok), int(s.valid),
            s.error or "", s.error_code, *(getattr(s, f) for f in VALUE_FIELDS),
            *(regs.get(n) for n in REGISTER_ADDRESSES))


def minute_params(m: MinuteRecord, run_pk: int) -> tuple:
    return (run_pk, m.address, m.minute_ms, m.sensor_type, m.n_samples, m.n_valid, m.mean, m.min,
            m.max, m.std, m.net_mean, m.body_temp_mean, int(m.complete))


def event_params(e: Event, run_pk: int | None) -> tuple:
    return (run_pk, e.t_ms, e.address, e.kind, e.severity, json.dumps(e.detail, ensure_ascii=False, default=str))


def sample_record(row) -> dict:
    """Fila tipada → registro de API, compatible con el payload del esquema 1."""
    pyrg = row["sensor_type"] == reg.PYRGEOMETER
    irr, lw, temp = row["irradiance"], row["lw_down"], row["body_temp_c"]
    calc = irr + blackbody_wm2(temp) if pyrg and irr is not None and temp is not None else None
    record = {
        "id": row["id"], "run_id": row["run_id"], "cycle": row["cycle"], "address": row["address"],
        "time_utc": iso_ms(row["t_ms"]), "t_ms": row["t_ms"], "sensor_type": row["sensor_type"],
        "ok": bool(row["ok"]), "valid": bool(row["valid"]), "error": row["error"],
        "error_code": row["error_code"],
    }
    record.update({f: row[f] for f in VALUE_FIELDS})
    record.update(
        raw_registers={str(n): row[c] for n, c in zip(REGISTER_ADDRESSES, REGISTER_COLUMNS) if row[c] is not None},
        measurement=lw if pyrg else irr,
        shortwave_wm2=None if pyrg else irr,
        net_longwave_wm2=irr if pyrg else None,
        lw_down_calc=calc,
        lw_check_delta=lw - calc if lw is not None and calc is not None else None,
        conversion_version=row["conversion_version"],
    )
    return record


def record_to_sample(record: dict) -> Sample:
    values = {k: record[k] for k in Sample.__dataclass_fields__ if k in record}
    values["time_utc"] = datetime.fromisoformat(record["time_utc"])
    values["raw_registers"] = {int(k): v for k, v in record.get("raw_registers", {}).items()}
    return Sample(**values)


def minute_record(row) -> dict:
    return {
        "run_id": row["run_id"], "address": row["address"], "minute_ms": row["minute_ms"],
        "minute_utc": iso_ms(row["minute_ms"]).replace(".000", ""), "sensor_type": row["sensor_type"],
        "n_samples": row["n_samples"], "n_valid": row["n_valid"], "mean_wm2": row["mean"],
        "min_wm2": row["min"], "max_wm2": row["max"], "std_wm2": row["std"],
        "net_mean_wm2": row["net_mean"], "body_temp_c_mean": row["body_temp_mean"],
        "complete": bool(row["complete"]),
    }


def event_record(row) -> dict:
    return {"id": row["id"], "run_id": row["run_id"], "t_ms": row["t_ms"], "time_utc": iso_ms(row["t_ms"]),
            "address": row["address"], "kind": row["kind"], "severity": row["severity"],
            "detail": json.loads(row["detail"])}


class WriterLock:
    """Lock del SO junto a la BD; se libera al cerrar el archivo o al morir el proceso."""

    def __init__(self, db_path: Path) -> None:
        self.path = Path(str(db_path) + ".writer.lock")
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if not self.handle.read(1):
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.release()
            return False

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def _schema_state(connection: sqlite3.Connection) -> tuple[int, bool]:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    tables = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    return version, bool(tables)


class Writer:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.connection: sqlite3.Connection | None = None
        self.lock: WriterLock | None = None
        self.run_pks: dict[str, int] = {}

    @traced("SQLiteWriter.open")
    def open(self) -> Result[None]:
        try:
            self.lock = WriterLock(self.path)
            if not self.lock.acquire():
                self.lock = None
                return Err(Failure("writer_active", "Ya hay una adquisición escribiendo esta BD"))
            self.connection = sqlite3.connect(self.path, timeout=0.5)
            self.connection.row_factory = sqlite3.Row
            version, tables = _schema_state(self.connection)
            if version == 1 and tables:
                self.close()
                return Err(Failure("schema_mismatch",
                                   "Esquema 1: ejecute `python -m smp22x migrate` con la adquisición detenida"))
            if version != SCHEMA_VERSION and (version != 0 or tables):
                self.close()
                return Err(Failure("schema_mismatch", f"Esquema {version}; se requiere {SCHEMA_VERSION}"))
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            if not tables:
                self.connection.executescript(SCHEMA)
            with self.connection:
                stamp, moment = now_utc(), now_ms()
                running = self.connection.execute("SELECT pk, id FROM runs WHERE state='running'").fetchall()
                self.connection.execute("UPDATE runs SET state='interrupted', ended_utc=? WHERE state='running'", (stamp,))
                self.connection.executemany(
                    "INSERT INTO events(run,t_ms,address,kind,severity,detail) VALUES(?,?,?,?,?,?)",
                    [event_params(Event(moment, "run_interrupted", "warning", None,
                                        {"run_id": r["id"], "reason": "La corrida anterior no terminó limpiamente"}),
                                  r["pk"]) for r in running])
            return Ok(None)
        except (sqlite3.Error, OSError) as exc:
            self.close()
            return database_error(exc) if isinstance(exc, sqlite3.Error) else Err(Failure("storage_failure", str(exc)))

    def _pk(self, run_id: str) -> int:
        if run_id not in self.run_pks:
            row = self.connection.execute("SELECT pk FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise sqlite3.IntegrityError(f"Corrida desconocida: {run_id}")
            self.run_pks[run_id] = row[0]
        return self.run_pks[run_id]

    def _insert(self, run_pk: int | None, samples: Iterable[tuple] = (), minutes: Iterable[MinuteRecord] = (),
                events: Iterable[Event] = ()) -> None:
        self.connection.executemany(
            f"INSERT INTO samples({','.join(SAMPLE_COLUMNS)}) VALUES({','.join('?' * len(SAMPLE_COLUMNS))})", samples)
        self.connection.executemany(
            f"INSERT INTO minutes({','.join(MINUTE_FIELDS)}) VALUES({','.join('?' * len(MINUTE_FIELDS))}) "
            "ON CONFLICT(run, address, minute_ms) DO NOTHING", [minute_params(m, run_pk) for m in minutes])
        self.connection.executemany(
            "INSERT INTO events(run,t_ms,address,kind,severity,detail) VALUES(?,?,?,?,?,?)",
            [event_params(e, run_pk) for e in events])

    @traced("SQLiteWriter.start", fields=("run_id",))
    def start(self, run_id: str, metadata: dict) -> Result[None]:
        try:
            assert self.connection is not None
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT INTO runs(id,started_utc,ended_utc,state,metadata,status,conversion_version) "
                    "VALUES(?,?,NULL,'running',?,?,?)",
                    (run_id, now_utc(), json.dumps(metadata, default=str),
                     json.dumps({"heartbeat_utc": now_utc()}), CONVERSION_VERSION))
                self.run_pks[run_id] = cursor.lastrowid
                self._insert(cursor.lastrowid, events=[Event(now_ms(), "run_started", "info", None, {
                    "run_id": run_id, "simulated": metadata.get("simulated"),
                    "label": (metadata.get("config") or {}).get("label")})])
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.commit_cycle", fields=("run_id", "cycle"))
    def commit_cycle(self, run_id: str, cycle: int, samples: list[Sample], status: dict,
                     minutes: Iterable[MinuteRecord] = (), events: Iterable[Event] = ()) -> Result[None]:
        try:
            assert self.connection is not None
            with self.connection:
                pk = self._pk(run_id)
                self._insert(pk, [sample_params(s, pk, cycle) for s in samples], minutes, events)
                self.connection.execute("UPDATE runs SET status=? WHERE pk=?",
                                        (json.dumps({**status, "heartbeat_utc": now_utc()}, default=str), pk))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.finish", fields=("run_id",))
    def finish(self, run_id: str, reason: str, metadata: dict, minutes: Iterable[MinuteRecord] = (),
               events: Iterable[Event] = ()) -> Result[None]:
        try:
            assert self.connection is not None
            with self.connection:
                pk = self._pk(run_id)
                self._insert(pk, minutes=minutes, events=[*events, Event(now_ms(), "run_finished", "info", None,
                                                                          {"run_id": run_id, "reason": reason})])
                self.connection.execute("UPDATE runs SET ended_utc=?,state=?,metadata=? WHERE pk=?",
                                        (now_utc(), reason, json.dumps(metadata, default=str), pk))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.metadata", fields=("run_id",))
    def metadata(self, run_id: str, metadata: dict) -> Result[None]:
        try:
            with self.connection:
                self.connection.execute("UPDATE runs SET metadata=? WHERE pk=?",
                                        (json.dumps(metadata, default=str), self._pk(run_id)))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.events", fields=("run_id",))
    def events(self, run_id: str | None, events: list[Event]) -> Result[None]:
        try:
            with self.connection:
                self._insert(self._pk(run_id) if run_id else None, events=events)
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    # ---------------------------------------------------------------- archive
    @traced("SQLiteWriter.record_archive")
    def record_archive(self, day: str, path: str, rows: int, sha256: str) -> Result[None]:
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO archive_days(day,path,rows,sha256,archived_ms,pruned_ms) VALUES(?,?,?,?,?,NULL) "
                    "ON CONFLICT(day) DO UPDATE SET path=excluded.path, rows=excluded.rows, "
                    "sha256=excluded.sha256, archived_ms=excluded.archived_ms, pruned_ms=NULL",
                    (day, path, rows, sha256, now_ms()))
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    def manifest(self) -> dict[str, dict]:
        return {r["day"]: dict(r) for r in self.connection.execute("SELECT * FROM archive_days")}

    def count_range(self, start_ms: int, end_ms: int) -> int:
        return self.connection.execute("SELECT count(*) FROM samples WHERE t_ms>=? AND t_ms<?",
                                       (start_ms, end_ms)).fetchone()[0]

    @traced("SQLiteWriter.prune_batch")
    def prune_batch(self, start_ms: int, end_ms: int, limit: int) -> Result[int]:
        """Borra hasta `limit` muestras del intervalo. Minutos y eventos se conservan."""
        try:
            with self.connection:
                cursor = self.connection.execute(
                    "DELETE FROM samples WHERE id IN (SELECT id FROM samples WHERE t_ms>=? AND t_ms<? LIMIT ?)",
                    (start_ms, end_ms, limit))
            return Ok(cursor.rowcount)
        except sqlite3.Error as exc:
            return database_error(exc)

    @traced("SQLiteWriter.mark_pruned")
    def mark_pruned(self, day: str, events: list[Event] = ()) -> Result[None]:
        try:
            with self.connection:
                self.connection.execute("UPDATE archive_days SET pruned_ms=? WHERE day=?", (now_ms(), day))
                self._insert(None, events=events)
            return Ok(None)
        except sqlite3.Error as exc:
            return database_error(exc)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.lock is not None:
            self.lock.release()
            self.lock = None


class Reader:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def query(self, action: Callable[[sqlite3.Connection], T]) -> Result[T]:
        if not self.path.exists():
            return Err(Failure("database_unavailable", "La adquisición todavía no ha creado la BD", True))
        conn = None
        try:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.5)
            conn.row_factory = sqlite3.Row
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                hint = "; ejecute `python -m smp22x migrate`" if version == 1 else ""
                return Err(Failure("schema_mismatch", f"Versión de esquema incompatible ({version}){hint}"))
            with conn:
                return Ok(action(conn))
        except sqlite3.Error as exc:
            return database_error(exc)
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _run(row) -> dict:
        data = {k: row[k] for k in row.keys() if k != "pk"}
        return {**data, "metadata": json.loads(row["metadata"]), "status": json.loads(row["status"])}

    @traced("SQLiteReader.runs")
    def runs(self, limit: int = 100) -> Result[list[dict]]:
        return self.query(lambda c: [self._run(r) for r in c.execute(
            "SELECT * FROM runs ORDER BY started_utc DESC, pk DESC LIMIT ?", (limit,))])

    @traced("SQLiteReader.run", fields=("run_id",))
    def run(self, run_id: str) -> Result[dict]:
        result = self.query(lambda c: c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        if isinstance(result, Err):
            return result
        if result.value is None:
            return Err(Failure("run_not_found", "Corrida no encontrada"))
        return Ok(self._run(result.value))

    @traced("SQLiteReader.samples", fields=("run_id", "after_id"))
    def samples(self, run_id: str, after_id: int = 0, limit: int = 1000, address: int | None = None,
                start_ms: int | None = None, end_ms: int | None = None, recent: bool = False) -> Result[dict]:
        def read(c):
            sql = f"{SAMPLE_SELECT} WHERE r.id=? AND s.id>?"
            params: list = [run_id, after_id]
            for name, op, value in (("s.address", "=", address), ("s.t_ms", ">=", start_ms), ("s.t_ms", "<=", end_ms)):
                if value is not None:
                    sql += f" AND {name}{op}?"
                    params.append(value)
            rows = c.execute(sql + (" ORDER BY s.id DESC LIMIT ?" if recent else " ORDER BY s.id LIMIT ?"),
                             (*params, limit + 1)).fetchall()
            chosen = list(reversed(rows[:limit])) if recent else rows[:limit]
            items = [sample_record(r) for r in chosen]
            return {"items": items, "next_cursor": items[-1]["id"] if items else after_id,
                    "has_more": not recent and len(rows) > limit,
                    "history_truncated": recent and len(rows) > limit}
        return self.query(read)

    def validate_cursor(self, run_id: str, cursor: int) -> Result[None]:
        if cursor == 0:
            return Ok(None)
        row = self.query(lambda c: c.execute(
            "SELECT r.id AS run_id FROM samples s JOIN runs r ON r.pk=s.run WHERE s.id=?", (cursor,)).fetchone())
        if isinstance(row, Err):
            return row
        if row.value is None:
            # Cursor podado por retención: válido si es anterior a las filas de esta corrida.
            first = self.query(lambda c: c.execute(
                "SELECT min(s.id) FROM samples s JOIN runs r ON r.pk=s.run WHERE r.id=?", (run_id,)).fetchone()[0])
            if isinstance(first, Err):
                return first
            if first.value is not None and cursor < first.value:
                return Ok(None)
            return Err(Failure("invalid_input", "El cursor no pertenece a esta corrida"))
        if row.value["run_id"] != run_id:
            return Err(Failure("invalid_input", "El cursor no pertenece a esta corrida"))
        return Ok(None)

    @traced("SQLiteReader.latest", fields=("run_id",))
    def latest(self, run_id: str) -> Result[list[dict]]:
        return self.query(lambda c: [sample_record(r) for r in c.execute(
            f"{SAMPLE_SELECT} WHERE s.id IN (SELECT max(s2.id) FROM samples s2 JOIN runs r2 ON r2.pk=s2.run "
            "WHERE r2.id=? GROUP BY s2.address) ORDER BY s.address", (run_id,))])

    @traced("SQLiteReader.minutes", fields=("run_id",))
    def minutes(self, run_id: str, limit: int = 1000) -> Result[list[dict]]:
        return self.query(lambda c: [minute_record(r) for r in c.execute(
            f"{MINUTE_SELECT} WHERE r.id=? ORDER BY m.minute_ms DESC, m.address LIMIT ?", (run_id, limit))])

    @traced("SQLiteReader.range_samples")
    def range_samples(self, start_ms: int, end_ms: int, addresses: list[int] | None = None,
                      after: tuple[int, int] | None = None, limit: int = 5000) -> Result[dict]:
        """Muestras de todas las corridas en [start, end), ordenadas por (t_ms, id), con cursor por clave."""
        def read(c):
            sql = f"{SAMPLE_SELECT} WHERE s.t_ms>=? AND s.t_ms<?"
            params: list = [start_ms, end_ms]
            if after is not None:
                sql += " AND (s.t_ms, s.id) > (?, ?)"
                params += list(after)
            if addresses:
                sql += f" AND s.address IN ({','.join('?' * len(addresses))})"
                params += list(addresses)
            rows = c.execute(sql + " ORDER BY s.t_ms, s.id LIMIT ?", (*params, limit + 1)).fetchall()
            items = [sample_record(r) for r in rows[:limit]]
            more = len(rows) > limit
            return {"items": items, "next": (items[-1]["t_ms"], items[-1]["id"]) if more and items else None}
        return self.query(read)

    @traced("SQLiteReader.range_minutes")
    def range_minutes(self, start_ms: int, end_ms: int, addresses: list[int] | None = None,
                      limit: int = 200_000) -> Result[dict]:
        def read(c):
            sql = f"{MINUTE_SELECT} WHERE m.minute_ms>=? AND m.minute_ms<?"
            params: list = [start_ms, end_ms]
            if addresses:
                sql += f" AND m.address IN ({','.join('?' * len(addresses))})"
                params += list(addresses)
            rows = c.execute(sql + " ORDER BY m.minute_ms, m.address, m.run LIMIT ?", (*params, limit + 1)).fetchall()
            return {"items": [minute_record(r) for r in rows[:limit]], "truncated": len(rows) > limit}
        return self.query(read)

    @traced("SQLiteReader.events")
    def events(self, start_ms: int, end_ms: int, limit: int = 2000) -> Result[list[dict]]:
        return self.query(lambda c: [event_record(r) for r in c.execute(
            "SELECT e.*, r.id AS run_id FROM events e LEFT JOIN runs r ON r.pk=e.run "
            "WHERE e.t_ms>=? AND e.t_ms<? ORDER BY e.t_ms DESC, e.id DESC LIMIT ?", (start_ms, end_ms, limit))])

    def latest_events(self, kinds: tuple[str, ...], limit: int = 1) -> Result[list[dict]]:
        return self.query(lambda c: [event_record(r) for r in c.execute(
            f"SELECT e.*, r.id AS run_id FROM events e LEFT JOIN runs r ON r.pk=e.run "
            f"WHERE e.kind IN ({','.join('?' * len(kinds))}) ORDER BY e.t_ms DESC, e.id DESC LIMIT ?",
            (*kinds, limit))])

    def manifest(self) -> Result[dict[str, dict]]:
        return self.query(lambda c: {r["day"]: dict(r) for r in c.execute("SELECT * FROM archive_days")})

    def count_range(self, start_ms: int, end_ms: int, addresses: list[int] | None = None) -> Result[int]:
        def read(c):
            sql, params = "SELECT count(*) FROM samples WHERE t_ms>=? AND t_ms<?", [start_ms, end_ms]
            if addresses:
                sql += f" AND address IN ({','.join('?' * len(addresses))})"
                params += list(addresses)
            return c.execute(sql, params).fetchone()[0]
        return self.query(read)

    @traced("SQLiteReader.runs_in_range")
    def runs_in_range(self, start_ms: int, end_ms: int) -> Result[list[dict]]:
        """Corridas con datos en el intervalo (por minutos o muestras), no por su hora de inicio."""
        return self.query(lambda c: [self._run(r) for r in c.execute(
            "SELECT * FROM runs WHERE pk IN (SELECT DISTINCT run FROM minutes WHERE minute_ms>=? AND minute_ms<? "
            "UNION SELECT DISTINCT run FROM samples WHERE t_ms>=? AND t_ms<?) ORDER BY started_utc, pk",
            (start_ms, end_ms, start_ms, end_ms))])

    def count_minutes(self, start_ms: int, end_ms: int, addresses: list[int] | None = None) -> Result[int]:
        def read(c):
            sql, params = "SELECT count(*) FROM minutes WHERE minute_ms>=? AND minute_ms<?", [start_ms, end_ms]
            if addresses:
                sql += f" AND address IN ({','.join('?' * len(addresses))})"
                params += list(addresses)
            return c.execute(sql, params).fetchone()[0]
        return self.query(read)

    def bounds(self) -> Result[dict]:
        return self.query(lambda c: dict(zip(("first_ms", "last_ms", "samples"), c.execute(
            "SELECT min(t_ms), max(t_ms), count(*) FROM samples").fetchone())))

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
        """CSV compatibles con los de la adquisición (raw_1s, minute_avg, run.json) de una corrida."""
        from .acquisition import RAW_COLUMNS, raw_row
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
                agg = MinuteAggregator(found.value["metadata"].get("config", {}).get("interval_s", 1))
                # Instantánea acotada al inicio: la adquisición activa puede seguir escribiendo.
                high = self.query(lambda c: c.execute(
                    "SELECT COALESCE(max(s.id),0) FROM samples s JOIN runs r ON r.pk=s.run WHERE r.id=?",
                    (run_id,)).fetchone()[0])
                if isinstance(high, Err):
                    return high
                cursor = 0
                while cursor < high.value:
                    page = self.samples(run_id, cursor, 1000)
                    if isinstance(page, Err):
                        return page
                    if not page.value["items"]:
                        break
                    for record in page.value["items"]:
                        if record["id"] > high.value:
                            break
                        s = record_to_sample(record)
                        rw.writerow(raw_row(record["cycle"], s))
                        for m in agg.add(s):
                            mw.writerow(m.csv_row())
                    cursor = page.value["next_cursor"]
                for m in agg.flush():
                    mw.writerow(m.csv_row())
            meta = {**found.value["metadata"], "export_max_id": high.value}
            (destination / "run.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
            return Ok(None)
        except OSError as exc:
            return Err(Failure("storage_failure", str(exc)))


# ------------------------------------------------------------------ migration
LEGACY_FIELDS = ("address", "time_utc", "ok", "error", "sensor_type", *VALUE_FIELDS, "error_code",
                 "cycle", "valid", "raw_registers")


def _legacy_sample(payload: dict) -> Sample:
    values = {k: payload.get(k) for k in Sample.__dataclass_fields__ if k in payload}
    values["time_utc"] = datetime.fromisoformat(payload["time_utc"])
    values["raw_registers"] = {int(k): v for k, v in (payload.get("raw_registers") or {}).items()}
    values["error"] = values.get("error") or ""
    return Sample(**values)


def _same(old, new) -> bool:
    if isinstance(old, float) or isinstance(new, float):
        return old is not None and new is not None and abs(old - new) <= 1e-9 * max(1.0, abs(old))
    return old == new


@traced("SQLiteMigration.migrate")
def migrate(path: Path, backup_dir: Path | None = None) -> Result[dict]:
    """Convierte una BD de esquema 1 (JSON por muestra) al esquema 2 tipado.

    Exige el lock de escritor, copia la BD original con la API de backup, escribe
    la nueva en un archivo temporal, verifica cada muestra campo por campo y solo
    entonces reemplaza el archivo. La copia previa nunca se borra.
    """
    path = path.resolve()
    if not path.exists():
        return Err(Failure("invalid_input", f"No existe {path}"))
    lock = WriterLock(path)
    if not lock.acquire():
        return Err(Failure("writer_active", "Detenga la adquisición antes de migrar"))
    old = new = None
    temp = path.with_name(path.name + ".v2-tmp")
    try:
        old = sqlite3.connect(path)
        old.row_factory = sqlite3.Row
        version, tables = _schema_state(old)
        if version == SCHEMA_VERSION:
            return Ok({"status": "already_current", "backup": None})
        if version != 1 or not tables:
            return Err(Failure("schema_mismatch", f"Solo se migra el esquema 1 (encontrado {version})"))
        old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = (backup_dir or path.parent) / f"{path.stem}.v1-backup-{stamp}{path.suffix}"
        target = sqlite3.connect(backup)
        try:
            old.backup(target)
        finally:
            target.close()
        if temp.exists():
            temp.unlink()
        new = sqlite3.connect(temp)
        new.row_factory = sqlite3.Row
        new.executescript(SCHEMA)
        report = {"status": "migrated", "backup": str(backup), "runs": 0, "samples": 0, "minutes": 0}
        with new:
            for run in old.execute("SELECT * FROM runs ORDER BY started_utc, rowid"):
                cursor = new.execute(
                    "INSERT INTO runs(id,started_utc,ended_utc,state,metadata,status,conversion_version) "
                    "VALUES(?,?,?,?,?,?,?)", (run["id"], run["started_utc"], run["ended_utc"], run["state"],
                                              run["metadata"], run["status"], CONVERSION_VERSION))
                pk = cursor.lastrowid
                report["runs"] += 1
                interval = (json.loads(run["metadata"]).get("config") or {}).get("interval_s") or 1
                agg = MinuteAggregator(interval)
                minutes: list[MinuteRecord] = []
                for row in old.execute("SELECT id, payload FROM samples WHERE run_id=? ORDER BY id", (run["id"],)):
                    payload = json.loads(row["payload"])
                    s = _legacy_sample(payload)
                    params = list(sample_params(s, pk, payload["cycle"]))
                    params[SAMPLE_COLUMNS.index("valid")] = int(bool(payload["valid"]))
                    new.execute(f"INSERT INTO samples(id,{','.join(SAMPLE_COLUMNS)}) "
                                f"VALUES(?,{','.join('?' * len(SAMPLE_COLUMNS))})", (row["id"], *params))
                    minutes.extend(agg.add(s))
                    report["samples"] += 1
                minutes.extend(agg.flush())
                new.executemany(f"INSERT INTO minutes({','.join(MINUTE_FIELDS)}) VALUES({','.join('?' * len(MINUTE_FIELDS))}) "
                                "ON CONFLICT DO NOTHING", [minute_params(m, pk) for m in minutes])
                report["minutes"] += len(minutes)
        # Verificación campo por campo antes de reemplazar.
        mismatches = 0
        for row in old.execute("SELECT id, payload FROM samples ORDER BY id"):
            payload = json.loads(row["payload"])
            migrated = new.execute(f"{SAMPLE_SELECT} WHERE s.id=?", (row["id"],)).fetchone()
            if migrated is None:
                mismatches += 1
                continue
            record = sample_record(migrated)
            for key in LEGACY_FIELDS:
                if key == "raw_registers":
                    ok = {str(k): v for k, v in (payload.get(key) or {}).items()} == record[key]
                else:
                    ok = _same(payload.get(key), record[key]) or (key == "error" and not payload.get(key) and not record[key])
                if not ok:
                    mismatches += 1
                    break
        if mismatches:
            return Err(Failure("migration_failure", f"{mismatches} muestras no coinciden; BD original intacta"))
        new.execute("PRAGMA journal_mode=DELETE")
        new.close()
        new = None
        old.close()
        old = None
        for suffix in ("-wal", "-shm"):
            side = Path(str(path) + suffix)
            if side.exists():
                side.unlink()
        os.replace(temp, path)
        return Ok(report)
    except (sqlite3.Error, OSError) as exc:
        return Err(Failure("migration_failure", f"{exc}; BD original intacta"))
    finally:
        for conn in (old, new):
            if conn is not None:
                conn.close()
        if temp.exists():
            temp.unlink()
        lock.release()

"""Archivo histórico en Parquet: un archivo por día UTC cerrado.

Los archivos se escriben con una conexión de solo lectura, en un temporal que se
renombra al terminar, y se identifican por número de filas y SHA-256. La base
activa solo borra un día cuando el archivo verificado contiene todas sus filas.
Si un día ya archivado recibe filas tardías (reloj corregido), el archivo se
reescribe uniendo lo archivado con lo nuevo, sin duplicar identificadores.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import __version__
from .events import from_ms, to_ms
from .result import Err, Failure, Ok, Result
from .storage import REGISTER_COLUMNS, SCHEMA_VERSION, VALUE_FIELDS, sample_record
from .tracing import traced

DAY_MS = 86_400_000
CLOSE_MARGIN_MS = 3_600_000   # un día se archiva una hora después de terminar
PRUNE_BATCH = 2000

INT_FIELDS = ("mode", "status", "scale_factor")
ARROW_SCHEMA = pa.schema(
    [("id", pa.int64()), ("run_id", pa.string()), ("conversion_version", pa.string()), ("cycle", pa.int64()),
     ("address", pa.int16()), ("time_utc", pa.timestamp("ms", tz="UTC")), ("sensor_type", pa.string()),
     ("ok", pa.bool_()), ("valid", pa.bool_()), ("error", pa.string()), ("error_code", pa.string())]
    + [(f, pa.int32() if f in INT_FIELDS else pa.float64()) for f in VALUE_FIELDS]
    + [(c, pa.int32()) for c in REGISTER_COLUMNS]
)
SELECT = (f"SELECT s.id, r.id AS run_id, r.conversion_version, s.cycle, s.address, s.t_ms, s.sensor_type, "
          f"s.ok, s.valid, s.error, s.error_code, {', '.join('s.' + f for f in VALUE_FIELDS)}, "
          f"{', '.join('s.' + c for c in REGISTER_COLUMNS)} FROM samples s JOIN runs r ON r.pk = s.run")


def day_name(t_ms: int) -> str:
    return from_ms(t_ms).date().isoformat()


def day_range(day: str) -> tuple[int, int]:
    start = to_ms(datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc))
    return start, start + DAY_MS


def day_path(root: Path, day: str) -> Path:
    return root / day[:4] / day[5:7] / f"{day}.parquet"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)


def _batch(rows: list[tuple]) -> pa.RecordBatch:
    columns = list(zip(*rows)) if rows else [[] for _ in ARROW_SCHEMA]
    arrays = []
    for field, values in zip(ARROW_SCHEMA, columns):
        if field.name == "time_utc":
            arrays.append(pa.array(values, type=pa.int64()).cast(field.type))
        elif field.type == pa.bool_():
            arrays.append(pa.array([bool(v) for v in values], type=field.type))
        else:
            arrays.append(pa.array(values, type=field.type))
    return pa.RecordBatch.from_arrays(arrays, schema=ARROW_SCHEMA)


@dataclass(frozen=True)
class ArchivedDay:
    day: str
    path: str
    rows: int
    sha256: str


@traced("Archive.write_day")
def write_day(db: Path, root: Path, day: str) -> Result[ArchivedDay]:
    """Escribe (o reescribe uniendo lo ya archivado) el Parquet de un día UTC."""
    start, end = day_range(day)
    target = day_path(root, day)
    temp = target.with_suffix(".parquet.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = pq.read_table(target) if target.exists() else None
        conn = _connect(db)
        try:
            rows = conn.execute(SELECT + " WHERE s.t_ms>=? AND s.t_ms<? ORDER BY s.t_ms, s.id", (start, end))
            conversions: set[str] = set()
            runs: set[str] = set()
            batches = []
            seen: set[int] = set()
            while chunk := rows.fetchmany(20_000):
                batches.append(_batch(chunk))
                seen.update(r[0] for r in chunk)
                conversions.update(r[2] for r in chunk)
                runs.update(r[1] for r in chunk)
        finally:
            conn.close()
        table = pa.Table.from_batches(batches, schema=ARROW_SCHEMA)
        if previous is not None:
            keep = previous.filter(pc.invert(pc.is_in(previous["id"], value_set=pa.array(list(seen), pa.int64()))))
            table = pa.concat_tables([keep.cast(ARROW_SCHEMA), table]).sort_by([("time_utc", "ascending"), ("id", "ascending")])
            conversions.update(keep["conversion_version"].to_pylist())
            runs.update(keep["run_id"].to_pylist())
        metadata = {"schema": "smp22x.samples", "schema_version": str(SCHEMA_VERSION), "day_utc": day,
                    "rows": str(table.num_rows), "conversion_versions": json.dumps(sorted(conversions)),
                    "run_ids": json.dumps(sorted(runs)), "software": __version__,
                    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": "Valores convertidos y palabras Modbus originales (reg_N). Sin correcciones adicionales."}
        table = table.replace_schema_metadata({k.encode(): v.encode() for k, v in metadata.items()})
        pq.write_table(table, temp, compression="zstd")
        os.replace(temp, target)
        return Ok(ArchivedDay(day, str(target), table.num_rows, sha256_file(target)))
    except (OSError, sqlite3.Error, pa.ArrowException) as exc:
        if temp.exists():
            temp.unlink()
        return Err(Failure("archive_failure", f"{day}: {exc}"))


@traced("Archive.verify_for_prune")
def verify_for_prune(db: Path, manifest: dict, day: str) -> Result[None]:
    """El archivo coincide con el manifiesto y contiene todas las filas del día en la base."""
    path = Path(manifest["path"])
    try:
        if not path.exists():
            return Err(Failure("archive_failure", f"{day}: falta {path}"))
        if sha256_file(path) != manifest["sha256"]:
            return Err(Failure("archive_failure", f"{day}: SHA-256 distinto al del manifiesto"))
        ids = pq.read_table(path, columns=["id"])["id"]
        if len(ids) != manifest["rows"]:
            return Err(Failure("archive_failure", f"{day}: el archivo no tiene las filas del manifiesto"))
        start, end = day_range(day)
        conn = _connect(db)
        try:
            in_db = {r[0] for r in conn.execute("SELECT id FROM samples WHERE t_ms>=? AND t_ms<?", (start, end))}
        finally:
            conn.close()
        missing = in_db - set(ids.to_pylist())
        if missing:
            return Err(Failure("archive_failure", f"{day}: {len(missing)} filas de la base no están archivadas"))
        return Ok(None)
    except (OSError, sqlite3.Error, pa.ArrowException) as exc:
        return Err(Failure("archive_failure", f"{day}: {exc}"))


def read_range(root: Path, start_ms: int, end_ms: int, addresses: list[int] | None = None,
               days: list[str] | None = None) -> list[dict]:
    """Registros archivados en [start, end) con el mismo formato que la API."""
    records: list[dict] = []
    for day in days if days is not None else days_between(start_ms, end_ms):
        path = day_path(root, day)
        if not path.exists():
            continue
        start = from_ms(start_ms)
        end = from_ms(end_ms)
        filters = [("time_utc", ">=", start), ("time_utc", "<", end)]
        if addresses:
            filters.append(("address", "in", list(addresses)))
        table = pq.read_table(path, filters=filters)
        for row in table.to_pylist():
            row["t_ms"] = to_ms(row.pop("time_utc"))
            records.append(sample_record(row))
    records.sort(key=lambda r: (r["t_ms"], r["id"]))
    return records


def days_between(start_ms: int, end_ms: int) -> list[str]:
    days, t = [], day_range(day_name(start_ms))[0]
    while t < end_ms:
        days.append(day_name(t))
        t += DAY_MS
    return days


def closed_days(first_ms: int | None, now: int) -> list[str]:
    if first_ms is None:
        return []
    return [d for d in days_between(first_ms, now) if day_range(d)[1] + CLOSE_MARGIN_MS <= now]


@traced("Archive.run_once")
def run_once(db: Path, root: Path, retention_days: int, now_ms: int | None = None) -> Result[dict]:
    """Archivo y poda sincrónicos, con el lock de escritor (adquisición detenida)."""
    from .events import Event
    from .storage import Writer
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    writer = Writer(db)
    opened = writer.open()
    if isinstance(opened, Err):
        return opened
    report = {"archived": [], "pruned": [], "failed": []}
    try:
        worker = ArchiveWorker(db, root, retention_days, clock=lambda: now / 1000)
        for _ in range(2):  # 1) escribir días cerrados, 2) verificar y podar los vencidos
            worker.scan()
            for kind, day, payload in worker.drain():
                if kind == "archived":
                    result = writer.record_archive(day, payload.path, payload.rows, payload.sha256)
                    if isinstance(result, Err):
                        return result
                    report["archived"].append(day)
                elif kind == "prune_ready":
                    start, end = payload
                    while True:
                        deleted = writer.prune_batch(start, end, PRUNE_BATCH * 10)
                        if isinstance(deleted, Err):
                            return deleted
                        if deleted.value == 0:
                            break
                    result = writer.mark_pruned(day, [Event(now, "archive_pruned", "info", None, {"day": day})])
                    if isinstance(result, Err):
                        return result
                    report["pruned"].append(day)
                else:
                    report["failed"].append({"day": day, "error": payload})
                if day:
                    worker.done(day)
        return Ok(report)
    finally:
        writer.close()


class ArchiveWorker(threading.Thread):
    """Hilo de archivo: escribe y verifica fuera del ciclo. El bucle principal registra y poda."""

    def __init__(self, db: Path, root: Path, retention_days: int, every_s: float = 3600,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(name="archive-worker", daemon=True)
        self.db, self.root, self.retention_days, self.every_s, self.clock = db, root, retention_days, every_s, clock
        self.messages: queue.Queue[tuple] = queue.Queue()
        self.stopping = threading.Event()
        self.pending: set[str] = set()   # días enviados al bucle y aún no reflejados en el manifiesto

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.scan()
            except Exception as exc:  # el hilo nunca debe morir en silencio
                self.messages.put(("failed", None, f"{type(exc).__name__}: {exc}"))
            self.stopping.wait(self.every_s)

    def scan(self) -> None:
        now = int(self.clock() * 1000)
        conn = _connect(self.db)
        try:
            first = conn.execute("SELECT min(t_ms) FROM samples").fetchone()[0]
            manifest = {r[0]: {"day": r[0], "path": r[1], "rows": r[2], "sha256": r[3], "pruned_ms": r[5]}
                        for r in conn.execute("SELECT day, path, rows, sha256, archived_ms, pruned_ms FROM archive_days")}
            counts = {}
            for day in closed_days(first, now):
                start, end = day_range(day)
                counts[day] = conn.execute("SELECT count(*) FROM samples WHERE t_ms>=? AND t_ms<?", (start, end)).fetchone()[0]
        finally:
            conn.close()
        for day, count in counts.items():
            if self.stopping.is_set() or day in self.pending or count == 0:
                continue
            entry = manifest.get(day)
            needs_write = entry is None or (entry["pruned_ms"] is None and entry["rows"] < count) or \
                (entry["pruned_ms"] is not None and count > 0)
            if needs_write:
                result = write_day(self.db, self.root, day)
                if isinstance(result, Err):
                    self.messages.put(("failed", day, result.error.message))
                else:
                    self.pending.add(day)
                    self.messages.put(("archived", day, result.value))
                continue
            expired = self.retention_days > 0 and day_range(day)[1] <= now - self.retention_days * DAY_MS
            if expired and entry["pruned_ms"] is None:
                check = verify_for_prune(self.db, entry, day)
                if isinstance(check, Err):
                    self.messages.put(("failed", day, check.error.message))
                else:
                    self.pending.add(day)
                    self.messages.put(("prune_ready", day, day_range(day)))

    def drain(self) -> list[tuple]:
        items = []
        while True:
            try:
                items.append(self.messages.get_nowait())
            except queue.Empty:
                return items

    def done(self, day: str) -> None:
        self.pending.discard(day)

    def stop(self) -> None:
        self.stopping.set()

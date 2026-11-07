"""Base del observatorio: configuración versionada y bitácora.

La escribe solo el proceso web. Las dos tablas son de solo agregar: una
configuración nueva es una versión nueva con fecha de vigencia, y una nota de
bitácora se anula con motivo, nunca se borra. La base de mediciones no cambia.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .events import iso_ms, now_ms, to_ms
from .result import Err, Failure, Ok, Result
from .tracing import traced

SCHEMA_VERSION = 1
RETROACTIVE_MS = 5 * 60_000
FUTURE_TOLERANCE_MS = 60_000
CATEGORIES = {
    "limpieza": "Limpieza de domo", "nivelacion": "Nivelación", "sombra": "Sombra u obstrucción",
    "desecante": "Desecante", "mantenimiento": "Mantenimiento", "solys2": "SOLYS2",
    "configuracion": "Configuración", "observacion": "Observación", "otro": "Otro",
}
SCHEMA = f"""
CREATE TABLE config_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, valid_from_ms INTEGER NOT NULL, created_ms INTEGER NOT NULL,
 author TEXT NOT NULL, note TEXT NOT NULL, config TEXT NOT NULL
);
CREATE TABLE logbook (
 id INTEGER PRIMARY KEY AUTOINCREMENT, t_ms INTEGER NOT NULL, created_ms INTEGER NOT NULL,
 author TEXT NOT NULL, category TEXT NOT NULL, addresses TEXT NOT NULL, text TEXT NOT NULL,
 source TEXT NOT NULL, voided_ms INTEGER, voided_by TEXT, void_reason TEXT
);
CREATE INDEX logbook_time ON logbook(t_ms);
PRAGMA user_version={SCHEMA_VERSION};
"""


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("fecha con zona horaria requerida")
    return value


class Site(BaseModel):
    name: str = Field("", max_length=80)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float = Field(0.0, ge=-500, le=9000)


class Instrument(BaseModel):
    address: int = Field(ge=1, le=247)
    shaded: bool = False
    label: str = Field("", max_length=60)


class QCConfig(BaseModel):
    comparison_pct: float = Field(5.0, gt=0, le=100)
    shading_kt_min: float = Field(0.6, gt=0, lt=1.5)
    shading_ratio_max: float = Field(0.8, gt=0, le=1.5)


class ObservatoryConfig(BaseModel):
    site: Site | None = None
    solys2_in_use: bool = False
    instruments: list[Instrument] = Field(default_factory=list, max_length=32)
    qc: QCConfig = Field(default_factory=QCConfig)

    @model_validator(mode="after")
    def unique_addresses(self):
        addresses = [i.address for i in self.instruments]
        if len(addresses) != len(set(addresses)):
            raise ValueError("direcciones repetidas en instrumentos")
        return self

    def instrument(self, address: int) -> Instrument | None:
        return next((i for i in self.instruments if i.address == address), None)


class Author(BaseModel):
    author: str = Field(min_length=1, max_length=60)

    @field_validator("author")
    @classmethod
    def strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("autor requerido")
        return value


class ConfigChange(Author):
    note: str = Field("", max_length=500)
    valid_from: datetime | None = None
    config: ObservatoryConfig

    @field_validator("valid_from")
    @classmethod
    def aware(cls, value):
        return _aware(value)


class LogbookEntryIn(Author):
    time_utc: datetime | None = None
    category: Literal[tuple(CATEGORIES)] = "observacion"  # type: ignore[valid-type]
    addresses: list[int] = Field(default_factory=list, max_length=32)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("time_utc")
    @classmethod
    def aware(cls, value):
        return _aware(value)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("texto requerido")
        return value


class VoidIn(Author):
    reason: str = Field(min_length=1, max_length=500)


def default_config(addresses: list[int]) -> ObservatoryConfig:
    return ObservatoryConfig(instruments=[Instrument(address=a) for a in addresses])


def describe_change(before: ObservatoryConfig, after: ObservatoryConfig) -> list[str]:
    """Resumen legible para la bitácora."""
    changes = []
    if before.solys2_in_use != after.solys2_in_use:
        changes.append(f"SOLYS2: {'en uso' if after.solys2_in_use else 'inactivo'}")
    if before.site != after.site:
        site = after.site
        changes.append("Sitio: sin configurar" if site is None else
                       f"Sitio: {site.latitude:.5f}, {site.longitude:.5f}, {site.altitude_m:.0f} m")
    for inst in after.instruments:
        old = before.instrument(inst.address)
        if old is None or old.shaded != inst.shaded:
            changes.append(f"#{inst.address}: {'con' if inst.shaded else 'sin'} bola de sombra")
        if old is not None and old.label != inst.label:
            changes.append(f"#{inst.address}: etiqueta «{inst.label}»")
    for old in before.instruments:
        if after.instrument(old.address) is None:
            changes.append(f"#{old.address}: retirado de la configuración")
    if before.qc != after.qc:
        q = after.qc
        changes.append(f"Umbrales: comparación {q.comparison_pct:g} %, sombreado kt > {q.shading_kt_min:g} "
                       f"y DHI/GHI > {q.shading_ratio_max:g}")
    return changes


def validate_change(change: ConfigChange, sensor_types: dict[int, str], now: int) -> tuple[list[str], int]:
    """Reglas que dependen del contexto. Devuelve (avisos, vigencia en ms) o lanza ValueError."""
    valid_from = to_ms(change.valid_from) if change.valid_from else now
    if valid_from > now + FUTURE_TOLERANCE_MS:
        raise ValueError("La vigencia no puede ser futura")
    if valid_from < now - RETROACTIVE_MS and not change.note.strip():
        raise ValueError("Una vigencia retroactiva exige una nota que explique el motivo")
    warnings = []
    for inst in change.config.instruments:
        kind = sensor_types.get(inst.address)
        if kind is None:
            warnings.append(f"#{inst.address} no figura en la última corrida")
        elif inst.shaded and kind != "pyranometer":
            if kind == "pyrgeometer":
                warnings.append(f"#{inst.address} es pirgeómetro: la bola de sombra no cambia su magnitud (Ld↓)")
    if change.config.solys2_in_use and not any(
            i.shaded and sensor_types.get(i.address, "pyranometer") == "pyranometer" for i in change.config.instruments):
        warnings.append("SOLYS2 en uso sin piranómetro con bola de sombra: no habrá DHI")
    if change.config.site is None:
        warnings.append("Sin sitio configurado: las pruebas que dependen del sol no aplican")
    return warnings, valid_from


class Observatory:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.lock = threading.Lock()

    def _connect(self, write: bool) -> sqlite3.Connection:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=2)
        else:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn

    def _read(self, action):
        if not self.path.exists():
            return Ok(action(None))
        conn = None
        try:
            conn = self._connect(False)
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                return Err(Failure("schema_mismatch", "Versión de la base del observatorio incompatible"))
            return Ok(action(conn))
        except sqlite3.Error as exc:
            return Err(Failure("storage_failure", str(exc)))
        finally:
            if conn is not None:
                conn.close()

    def _write(self, action):
        with self.lock:
            conn = None
            try:
                conn = self._connect(True)
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                if version == 0 and not tables:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(SCHEMA)
                elif version != SCHEMA_VERSION:
                    return Err(Failure("schema_mismatch", "Versión de la base del observatorio incompatible"))
                conn.execute("PRAGMA synchronous=FULL")
                with conn:
                    return Ok(action(conn))
            except sqlite3.Error as exc:
                return Err(Failure("storage_failure", str(exc)))
            finally:
                if conn is not None:
                    conn.close()

    @staticmethod
    def _version(row) -> dict:
        return {"id": row["id"], "valid_from_ms": row["valid_from_ms"], "valid_from": iso_ms(row["valid_from_ms"]),
                "created": iso_ms(row["created_ms"]), "author": row["author"], "note": row["note"],
                "config": json.loads(row["config"])}

    @staticmethod
    def _entry(row) -> dict:
        return {"id": row["id"], "t_ms": row["t_ms"], "time_utc": iso_ms(row["t_ms"]),
                "created": iso_ms(row["created_ms"]), "author": row["author"], "category": row["category"],
                "category_label": CATEGORIES.get(row["category"], row["category"]),
                "addresses": json.loads(row["addresses"]), "text": row["text"], "source": row["source"],
                "voided": row["voided_ms"] is not None,
                "voided_at": iso_ms(row["voided_ms"]) if row["voided_ms"] is not None else None,
                "voided_by": row["voided_by"], "void_reason": row["void_reason"]}

    @traced("Observatory.versions")
    def versions(self) -> Result[list[dict]]:
        return self._read(lambda c: [] if c is None else [self._version(r) for r in c.execute(
            "SELECT * FROM config_versions ORDER BY valid_from_ms, id")])

    @traced("Observatory.add_version")
    def add_version(self, change: ConfigChange, previous: ObservatoryConfig, valid_from: int) -> Result[dict]:
        summary = describe_change(previous, change.config) or ["Sin cambios de contenido"]
        def write(c):
            created = now_ms()
            cursor = c.execute(
                "INSERT INTO config_versions(valid_from_ms,created_ms,author,note,config) VALUES(?,?,?,?,?)",
                (valid_from, created, change.author, change.note.strip(), change.config.model_dump_json()))
            text = "; ".join(summary) + (f". Nota: {change.note.strip()}" if change.note.strip() else "")
            category = "solys2" if previous.solys2_in_use != change.config.solys2_in_use else "configuracion"
            c.execute("INSERT INTO logbook(t_ms,created_ms,author,category,addresses,text,source) VALUES(?,?,?,?,?,?,?)",
                      (valid_from, created, change.author, category, "[]", text, f"config:{cursor.lastrowid}"))
            return self._version(c.execute("SELECT * FROM config_versions WHERE id=?", (cursor.lastrowid,)).fetchone())
        return self._write(write)

    @traced("Observatory.logbook")
    def logbook(self, start_ms: int, end_ms: int, include_voided: bool = True, limit: int = 2000) -> Result[list[dict]]:
        sql = "SELECT * FROM logbook WHERE t_ms>=? AND t_ms<?" + ("" if include_voided else " AND voided_ms IS NULL")
        return self._read(lambda c: [] if c is None else [self._entry(r) for r in c.execute(
            sql + " ORDER BY t_ms DESC, id DESC LIMIT ?", (start_ms, end_ms, limit))])

    @traced("Observatory.add_entry")
    def add_entry(self, entry: LogbookEntryIn, now: int) -> Result[dict]:
        t = to_ms(entry.time_utc) if entry.time_utc else now
        if t > now + FUTURE_TOLERANCE_MS:
            return Err(Failure("invalid_input", "La hora de la nota no puede ser futura"))
        def write(c):
            cursor = c.execute(
                "INSERT INTO logbook(t_ms,created_ms,author,category,addresses,text,source) VALUES(?,?,?,?,?,?,?)",
                (t, now, entry.author, entry.category, json.dumps(sorted(set(entry.addresses))),
                 entry.text.strip(), "manual"))
            return self._entry(c.execute("SELECT * FROM logbook WHERE id=?", (cursor.lastrowid,)).fetchone())
        return self._write(write)

    @traced("Observatory.void_entry")
    def void_entry(self, entry_id: int, body: VoidIn, now: int) -> Result[dict]:
        def write(c):
            row = c.execute("SELECT * FROM logbook WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                return None
            if row["voided_ms"] is not None:
                return "already"
            c.execute("UPDATE logbook SET voided_ms=?, voided_by=?, void_reason=? WHERE id=?",
                      (now, body.author, body.reason.strip(), entry_id))
            return self._entry(c.execute("SELECT * FROM logbook WHERE id=?", (entry_id,)).fetchone())
        result = self._write(write)
        if isinstance(result, Err):
            return result
        if result.value is None:
            return Err(Failure("not_found", "Nota no encontrada"))
        if result.value == "already":
            return Err(Failure("invalid_input", "La nota ya estaba anulada"))
        return result

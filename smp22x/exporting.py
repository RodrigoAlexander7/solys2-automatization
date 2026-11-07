"""Exportación por intervalo: CSV en streaming y ZIP con metadatos.

Las columnas medidas son las del sensor en unidades físicas, sin correcciones.
Rol, geometría solar, QC y DNI estimada son derivados y van en columnas propias.
"""
from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import __version__, qc
from . import registers as reg
from .analysis import ConfigTimeline, annotate_minutes, annotate_samples
from .archive import DAY_MS, day_range, days_between
from .events import from_ms, iso_ms
from .history import iter_samples
from .minutes import fmt
from .result import Err, StorageFailure
from .storage import Reader

QC_COLUMNS = ["qc_ppl", "qc_erl", "qc_ratio", "qc_shading", "qc_comparison", "qc_summary", "qc_notes"]
DERIVED_COLUMNS = ["solar_zenith_deg", "kt", "dhi_ghi_ratio", "dni_est_wm2"]
SAMPLE_COLUMNS = [
    "time_utc", "time_local", "run_id", "cycle", "address", "label", "sensor_type", "role",
    "measurement_wm2", "irradiance_wm2", "raw_irradiance_wm2", "lw_down_wm2", "raw_lw_down_wm2",
    "lw_down_calc_wm2", "stdev_wm2", "body_temp_c", "supply_v", "rh_pct", "pressure_hpa",
    "mode", "status_hex", "status_flags", "valid", "rtt_ms", "error",
    *DERIVED_COLUMNS, *QC_COLUMNS, "config_version",
]
MINUTE_COLUMNS = [
    "minute_utc", "minute_local", "run_id", "address", "label", "sensor_type", "role",
    "n_samples", "n_valid", "complete", "mean_wm2", "min_wm2", "max_wm2", "std_wm2",
    "net_mean_wm2", "body_temp_c_mean", *DERIVED_COLUMNS, *QC_COLUMNS, "config_version",
]
MAX_SPAN_MS = {"1s": 31 * DAY_MS, "1min": 366 * DAY_MS}


def _qc_cells(r: dict) -> list:
    q = r["qc"]
    return [q["ppl"], q["erl"], q["ratio"], q["shading"], q["comparison"], q["summary"], "; ".join(q["notes"])]


def _derived(r: dict) -> list:
    return [fmt(r["solar_zenith_deg"], 3), fmt(r["kt"], 4), fmt(r["dhi_ghi_ratio"], 4), fmt(r["dni_est_wm2"], 2)]


def sample_row(r: dict) -> list:
    status = r["status"]
    return [
        r["time_utc"], from_ms(r["t_ms"]).astimezone().isoformat(timespec="milliseconds"), r["run_id"],
        r["cycle"], r["address"], r["label"], r["sensor_type"], r["role"],
        fmt(r["measurement"], 2), fmt(r["irradiance"], 2), fmt(r["raw_irradiance"], 2), fmt(r["lw_down"], 2),
        fmt(r["raw_lw_down"], 2), fmt(r["lw_down_calc"], 2), fmt(r["stdev"]), fmt(r["body_temp_c"]),
        fmt(r["supply_v"]), fmt(r["rh_pct"]), fmt(r["pressure_hpa"]),
        "" if r["mode"] is None else r["mode"], "" if status is None else f"0x{status:04X}",
        "|".join(reg.decode_status(status)) if status else "", int(r["valid"]), fmt(r["rtt_ms"]), r["error"],
        *_derived(r), *_qc_cells(r), "" if r["config_version"] is None else r["config_version"],
    ]


def minute_row(r: dict) -> list:
    return [
        r["minute_utc"], from_ms(r["minute_ms"]).astimezone().isoformat(timespec="seconds"), r["run_id"],
        r["address"], r["label"], r["sensor_type"], r["role"], r["n_samples"], r["n_valid"], int(r["complete"]),
        fmt(r["mean_wm2"], 2), fmt(r["min_wm2"]), fmt(r["max_wm2"]), fmt(r["std_wm2"], 2),
        fmt(r["net_mean_wm2"], 2), fmt(r["body_temp_c_mean"], 2),
        *_derived(r), *_qc_cells(r), "" if r["config_version"] is None else r["config_version"],
    ]


def _csv(rows: list[list]) -> str:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def iter_minutes(reader: Reader, start_ms: int, end_ms: int, addresses: list[int] | None) -> Iterator[list[dict]]:
    for day in days_between(start_ms, end_ms):
        d_start, d_end = day_range(day)
        result = reader.range_minutes(max(start_ms, d_start), min(end_ms, d_end), addresses)
        if isinstance(result, Err):
            raise StorageFailure(result.error)
        if result.value["items"]:
            yield result.value["items"]


def iter_csv(reader: Reader, archive_dir: Path, timeline: ConfigTimeline, start_ms: int, end_ms: int,
             addresses: list[int] | None, resolution: str, include_invalid: bool) -> Iterator[str]:
    if resolution == "1min":
        yield _csv([MINUTE_COLUMNS])
        for page in iter_minutes(reader, start_ms, end_ms, addresses):
            rows = annotate_minutes(page, timeline)
            yield _csv([minute_row(r) for r in rows if include_invalid or r["n_valid"] > 0])
    else:
        yield _csv([SAMPLE_COLUMNS])
        for page in iter_samples(reader, archive_dir, start_ms, end_ms, addresses):
            rows = annotate_samples(page, timeline)
            yield _csv([sample_row(r) for r in rows if include_invalid or r["valid"]])


LEEME = """Exportación del observatorio de radiometría
============================================

Archivos
- {data_file}: datos del intervalo pedido.
- metadata.json: consulta, instrumentos, configuraciones vigentes, reloj y definiciones de QC.
- bitacora.csv: notas de la bitácora dentro del intervalo (incluye anuladas, marcadas).
- eventos.csv: eventos de adquisición (flags, puerto, reloj, archivo) del intervalo.

Convenciones
- time_utc / minute_utc: UTC, ISO 8601. La hora local es la del PC que exportó.
- La hora de cada muestra la pone el PC al terminar la lectura; consulte los eventos
  clock_check para conocer el desfase medido del reloj.
- Las lecturas de un ciclo son secuenciales, no simultáneas.
- measurement_wm2: magnitud principal tal como la entrega el sensor (global en
  piranómetros, Ld↓ en el pirgeómetro), sin corrección adicional del programa.
- valid=0: el sensor no respondió, no estaba en modo normal o tenía flags que invalidan
  el dato. Un valor vacío es ausente, no cero.
- role: GHI global, DHI difusa (piranómetro con bola de sombra y SOLYS2 en uso),
  LWD onda larga descendente, SHADE_OFF bola de sombra con SOLYS2 inactivo.
- Derivados (no son mediciones): solar_zenith_deg, kt, dhi_ghi_ratio, dni_est_wm2,
  lw_down_calc_wm2 (neta + σT⁴).
- QC: ok / fail / na (no aplica). qc_summary: bad (falla física), suspect (otra falla),
  ok o na. El QC nunca modifica el valor medido. Ver metadata.json → qc.
"""


def _metadata(reader: Reader, observatory_versions: list[dict], timeline: ConfigTimeline, query: dict,
              clock_events: list[dict]) -> dict:
    runs = reader.runs_in_range(query["start_ms"], query["end_ms"])
    in_range = []
    for run in [] if isinstance(runs, Err) else runs.value:
        meta = run["metadata"]
        in_range.append({"id": run["id"], "state": run["state"], "started_utc": run["started_utc"],
                         "ended_utc": run["ended_utc"], "simulated": meta.get("simulated"),
                         "bus": meta.get("bus"), "config": meta.get("config"),
                         "sensors": meta.get("sensors"), "conversion_version": run.get("conversion_version")})
    relevant = [v for v in observatory_versions if v["valid_from_ms"] < query["end_ms"]]
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "software": f"smp22x {__version__}",
        "query": {k: v for k, v in query.items() if not k.endswith("_ms")},
        "runs": in_range,
        "config_default": timeline.default.model_dump(),
        "config_versions": relevant,
        "clock_checks": clock_events,
        "qc": {
            "codes": {"ok": "pasa", "fail": "falla", "na": "no aplica"},
            "tests": qc.TEST_LABELS,
            "source_bsrn": "Long, C. N. y Dutton, E. G. (2010), BSRN Global Network recommended QC tests, V2.0",
            "solar_constant_wm2": 1361.0,
            "project_tests": "Sombreado y comparación entre iguales: decisiones del proyecto con umbrales en config_versions[*].config.qc",
        },
        "units": {"irradiance": "W/m²", "temperature": "°C", "voltage": "V", "rtt": "ms", "angles": "grados"},
    }


def build_zip(reader: Reader, archive_dir: Path, timeline: ConfigTimeline, observatory_versions: list[dict],
              logbook: list[dict], events: list[dict], query: dict) -> tempfile.SpooledTemporaryFile:
    resolution = query["resolution"]
    data_file = f"datos_{resolution}.csv"
    spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open(data_file, "w") as handle:
            for chunk in iter_csv(reader, archive_dir, timeline, query["start_ms"], query["end_ms"],
                                  query["addresses"], resolution, query["include_invalid"]):
                handle.write(chunk.encode("utf-8"))
        clock_events = [e for e in events if e["kind"] in ("clock_check", "clock_unavailable")]
        archive.writestr("metadata.json", json.dumps(
            _metadata(reader, observatory_versions, timeline, query, clock_events), indent=2, ensure_ascii=False,
            default=str))
        archive.writestr("bitacora.csv", _csv(
            [["time_utc", "category", "addresses", "author", "text", "source", "voided", "void_reason"]]
            + [[e["time_utc"], e["category"], " ".join(map(str, e["addresses"])), e["author"], e["text"],
                e["source"], int(e["voided"]), e["void_reason"] or ""] for e in sorted(logbook, key=lambda e: e["t_ms"])]))
        archive.writestr("eventos.csv", _csv(
            [["time_utc", "run_id", "address", "kind", "severity", "detail"]]
            + [[e["time_utc"], e["run_id"] or "", "" if e["address"] is None else e["address"], e["kind"],
                e["severity"], json.dumps(e["detail"], ensure_ascii=False)] for e in sorted(events, key=lambda e: e["t_ms"])]))
        archive.writestr("LEEME.txt", LEEME.format(data_file=data_file))
    spool.seek(0)
    return spool


def file_stem(query: dict) -> str:
    start = iso_ms(query["start_ms"])[:16].replace(":", "").replace("-", "")
    end = iso_ms(query["end_ms"])[:16].replace(":", "").replace("-", "")
    return f"radiometria_{start}Z_{end}Z_{query['resolution']}"

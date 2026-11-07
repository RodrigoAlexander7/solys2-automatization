"""Web process. Never opens a serial port or starts acquisition.

Reads the acquisition database; writes only the observatory database
(configuration versions and logbook). Every request must carry a local Host
header, and writes must come from the dashboard's own origin as JSON.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import __version__
from .analysis import ConfigTimeline, annotate_minutes, annotate_samples, comparisons
from .config import Settings, settings
from .decimate import decimate
from .events import describe, iso_ms, now_ms
from .exporting import MAX_SPAN_MS, build_zip, file_stem, iter_csv, iter_minutes
from .history import range_samples
from .metrics import age_s
from .observatory import (CATEGORIES, ConfigChange, LogbookEntryIn, Observatory, QCConfig, VoidIn,
                          validate_change)
from .qc import FAIL, TEST_LABELS
from .result import Err, StorageFailure
from .storage import Reader
from .tracing import emit, trace_id, trace_path

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
STATUS_FOR = {"run_not_found": 404, "not_found": 404, "invalid_input": 422, "forbidden": 403,
              "schema_mismatch": 503, "database_unavailable": 503, "database_busy": 503}
SLIM_FIELDS = ("id", "run_id", "cycle", "address", "t_ms", "sensor_type", "ok", "valid", "measurement",
               "lw_down_calc", "body_temp_c", "supply_v", "rtt_ms", "status", "role", "label",
               "dni_est_wm2", "dhi_ghi_ratio", "solar_zenith_deg")


class ApiFailure(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def unpack(result):
    if isinstance(result, Err):
        e = result.error
        raise ApiFailure(STATUS_FOR.get(e.code, 503), e.code, e.message, e.retryable)
    return result.value


def utc_ms(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except ValueError:
        raise ApiFailure(422, "invalid_input", "Fecha ISO 8601 con zona requerida")


def parse_range(start: str | None, end: str | None, default_span_ms: int, max_span_ms: int) -> tuple[int, int]:
    end_ms = utc_ms(end) or now_ms()
    start_ms = utc_ms(start) if start is not None else end_ms - default_span_ms
    if start_ms >= end_ms:
        raise ApiFailure(422, "invalid_input", "start debe ser anterior a end")
    if end_ms - start_ms > max_span_ms:
        raise ApiFailure(422, "invalid_input", f"Intervalo demasiado largo (máximo {max_span_ms // DAY_MS or 1} días)")
    return start_ms, end_ms


def parse_addresses(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        addresses = sorted({int(x) for x in value.split(",") if x.strip()})
    except ValueError:
        raise ApiFailure(422, "invalid_input", "addresses: lista de enteros separada por comas")
    if not addresses or any(not 1 <= a <= 247 for a in addresses):
        raise ApiFailure(422, "invalid_input", "addresses fuera de 1–247")
    return addresses


def event(name: str, data: dict, cursor: int | None = None) -> str:
    return (f"id: {cursor}\n" if cursor is not None else "") + f"event: {name}\ndata: {json.dumps(data, allow_nan=False)}\n\n"


def _host(header: str) -> str:
    h = header.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _failure(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": code, "message": message, "retryable": False},
                         "trace_id": trace_id.get()}, status_code=status, headers={"X-Trace-ID": trace_id.get()})


class FrontendFiles(StaticFiles):
    """HTML siempre revalidado; assets con hash, inmutables. Evita páginas viejas tras actualizar."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = str(full_path)
        if "/_app/immutable/" in path.replace("\\", "/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def slim(record: dict) -> dict:
    item = {k: record.get(k) for k in SLIM_FIELDS}
    item["qc"] = record["qc"]["summary"]
    item["qc_failed"] = [t for t in TEST_LABELS if record["qc"].get(t) == FAIL]
    return item


def create_app(config: Settings | None = None) -> FastAPI:
    cfg = config or settings()
    reader = Reader(cfg.db)
    observatory = Observatory(cfg.observatory)
    figure_lock = threading.Lock()
    allowed_hosts = {h.lower().strip("[]") for h in cfg.allowed_hosts}
    app = FastAPI(title="Radiometría local", version=__version__)

    @app.middleware("http")
    async def protect_and_trace(request: Request, call_next):
        token = trace_id.set(uuid.uuid4().hex)
        path = trace_path.set((f"HTTP {request.method} {request.url.path}",))
        started = time.perf_counter()
        try:
            host_header = request.headers.get("host", "")
            if _host(host_header) not in allowed_hosts:
                response = _failure(403, "forbidden", "Host no permitido: el dashboard solo atiende pedidos locales")
            elif request.method not in SAFE_METHODS and (
                    urlparse(request.headers.get("origin", "")).netloc.lower() != host_header.lower()):
                response = _failure(403, "forbidden", "Origen no permitido para escribir")
            elif request.method not in SAFE_METHODS and not request.headers.get(
                    "content-type", "").lower().startswith("application/json"):
                response = _failure(415, "invalid_input", "Las escrituras deben ser JSON")
            else:
                response = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id.get()
            emit("HTTP", "FAILED" if response.status_code >= 400 else "OK",
                 (time.perf_counter() - started) * 1000, http_status=response.status_code)
            return response
        except Exception:
            import traceback
            emit("HTTP", "FAILED", (time.perf_counter() - started) * 1000,
                 error="internal_error", traceback=traceback.format_exc())
            return _failure(500, "internal_error", "Error interno")
        finally:
            trace_path.reset(path)
            trace_id.reset(token)

    @app.exception_handler(ApiFailure)
    async def failure_handler(request, exc):
        return JSONResponse({"ok": False, "error": {"code": exc.code, "message": exc.message,
                            "retryable": exc.retryable}, "trace_id": trace_id.get()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        detail = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ())[1:])}: {e.get('msg')}" for e in exc.errors()[:4])
        return await failure_handler(request, ApiFailure(422, "invalid_input",
                                                         f"Parámetros fuera de rango o inválidos ({detail})"))

    @app.exception_handler(StorageFailure)
    async def storage_handler(request, exc):
        return await failure_handler(request, ApiFailure(STATUS_FOR.get(exc.error.code, 503), exc.error.code,
                                                         exc.error.message, exc.error.retryable))

    # ------------------------------------------------------------ helpers
    def latest_run() -> dict | None:
        runs = reader.runs(1)
        if isinstance(runs, Err):
            if runs.error.code == "database_unavailable":
                return None
            unpack(runs)
        return runs.value[0] if runs.value else None

    def sensor_types(run: dict | None = None) -> dict[int, str]:
        run = run if run is not None else latest_run()
        if run is None:
            return {}
        c = run["metadata"].get("config") or {}
        pyrg = set(c.get("pyrgeometers") or [])
        return {a: "pyrgeometer" if a in pyrg else "pyranometer" for a in c.get("addresses") or []}

    def timeline(extra: list[int] | tuple = ()) -> ConfigTimeline:
        return ConfigTimeline.build(unpack(observatory.versions()), [*sensor_types(), *extra])

    def status_payload(run: dict) -> dict:
        latest = unpack(reader.latest(run["id"]))
        tl = timeline([s["address"] for s in latest])
        annotate_samples(latest, tl)
        for s in latest:
            s["age_s"] = age_s(s["time_utc"])
            s["stale"] = s["age_s"] > cfg.stale_s
        heartbeat = run["status"].get("heartbeat_utc", run["started_utc"])
        running = run["state"] == "running"
        acquisition_stale = running and age_s(heartbeat) > cfg.stale_s
        now = now_ms()
        current = tl.current(now)
        pairs = comparisons(latest)
        alerts = []
        if acquisition_stale:
            alerts.append({"level": "warning", "text": "La adquisición no confirma actividad reciente"})
        clock = clock_payload(run, running)
        if clock["warning"]:
            alerts.append({"level": "warning", "text": clock["warning"]})
        for s in latest:
            if s["role"] == "DHI" and s["qc"]["shading"] == FAIL:
                alerts.append({"level": "warning", "text": f"{s['label']}: la prueba de sombreado falla; "
                               "revise que la bola del SOLYS2 tape el sol"})
            if s["qc"]["summary"] == "bad":
                alerts.append({"level": "error", "text": f"{s['label']}: valor fuera de límites físicamente posibles"})
        archive = run["status"].get("archive") or {}
        if archive.get("last_error"):
            alerts.append({"level": "warning", "text": f"Archivo: {archive['last_error']}"})
        legacy = next((p for p in pairs if p["role"] == "GHI"), None)
        return {"run": run, "latest": latest, "comparisons": pairs,
                "comparison": {"difference_wm2": legacy["difference_wm2"] if legacy else None,
                               "difference_pct": legacy["difference_pct"] if legacy else None},
                "config": current.model_dump(), "config_version": tl.version_id(now),
                "clock": clock, "alerts": alerts, "heartbeat_age_s": age_s(heartbeat), "stale_s": cfg.stale_s,
                "acquisition_stale": acquisition_stale}

    def clock_payload(run: dict, running: bool) -> dict:
        status = run["status"]
        check = status.get("clock")
        enabled = bool(status.get("clock_check_enabled"))
        warning = None
        age = None if not check else max(0, (now_ms() - check["t_ms"]) / 1000)
        if not running:
            pass
        elif not enabled:
            warning = "Comprobación del reloj deshabilitada (sin servidor NTP configurado)"
        elif check is None:
            if age_s(run["started_utc"]) > 120:
                warning = "Todavía no hay una comprobación del reloj"
        elif not check["ok"]:
            warning = f"Reloj sin referencia NTP: {check['error']}"
        elif not check["within_limit"]:
            warning = f"El reloj del PC se desvía {check['offset_s']:+.3f} s (umbral {check['max_offset_s']:g} s)"
        elif age is not None and age > 3 * cfg.clock_check_s:
            warning = f"Última comprobación del reloj hace {age / 60:.0f} min"
        return {"enabled": enabled, "check": check, "age_s": age, "warning": warning}

    # ------------------------------------------------------------ lectura
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "database_ready": not isinstance(reader.runs(1), Err),
                "observatory_ready": not isinstance(observatory.versions(), Err)}

    @app.get("/api/runs")
    def runs(limit: int = Query(100, ge=1, le=1000)):
        return {"items": unpack(reader.runs(limit))}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        return unpack(reader.run(run_id))

    @app.get("/api/runs/{run_id}/samples")
    def samples(run_id: str, after_id: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000),
                address: int | None = Query(None, ge=1, le=247), start: str | None = None,
                end: str | None = None, recent: bool = False):
        unpack(reader.run(run_id))
        unpack(reader.validate_cursor(run_id, after_id))
        start_ms, end_ms = utc_ms(start), utc_ms(end)
        if start_ms is not None and end_ms is not None and start_ms > end_ms:
            raise ApiFailure(422, "invalid_input", "start debe ser anterior a end")
        page = unpack(reader.samples(run_id, after_id, limit, address, start_ms, end_ms, recent))
        annotate_samples(page["items"], timeline([r["address"] for r in page["items"]]))
        return page

    @app.get("/api/runs/{run_id}/minutes")
    def minutes(run_id: str, limit: int = Query(1000, ge=1, le=5000)):
        unpack(reader.run(run_id))
        items = unpack(reader.minutes(run_id, limit))
        return {"items": annotate_minutes(items, timeline([r["address"] for r in items]))}

    @app.get("/api/runs/{run_id}/status")
    def status(run_id: str):
        return status_payload(unpack(reader.run(run_id)))

    @app.get("/api/live")
    def live():
        current = latest_run()
        if current is None:
            return {"run": None, "latest": [], "comparisons": [], "alerts": [], "config": timeline().current(now_ms()).model_dump(),
                    "clock": {"enabled": False, "check": None, "age_s": None, "warning": None}}
        return status_payload(current)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(request: Request, run_id: str, after_id: int = Query(0, ge=0), once: bool = False):
        unpack(await run_in_threadpool(reader.run, run_id))
        header = request.headers.get("last-event-id")
        try:
            cursor = max(after_id, int(header)) if header is not None else after_id
            if cursor < 0:
                raise ValueError()
        except ValueError:
            raise ApiFailure(422, "invalid_input", "Last-Event-ID inválido")
        unpack(await run_in_threadpool(reader.validate_cursor, run_id, cursor))

        def page_at(position: int):
            result = reader.samples(run_id, position, 1000)
            if isinstance(result, Err):
                return result
            items = result.value["items"]
            if items:
                annotate_samples(items, timeline([r["address"] for r in items]))
                result.value["items"] = [slim(r) for r in items]
            return result

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                try:
                    result = await run_in_threadpool(page_at, cursor)
                except Exception as exc:  # p. ej. base del observatorio ilegible
                    yield event("failure", {"error": type(exc).__name__, "trace_id": trace_id.get()})
                    return
                if isinstance(result, Err):
                    yield event("failure", {"error": result.error.code, "trace_id": trace_id.get()})
                    return
                page = result.value
                if page["items"]:
                    cursor = page["next_cursor"]
                    yield event("samples", page, cursor)
                else:
                    yield event("heartbeat", {"cursor": cursor})
                if once:
                    return
                await asyncio.sleep(0 if page["has_more"] else cfg.poll_s)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/series")
    def series(start: str | None = None, end: str | None = None, addresses: str | None = None,
               resolution: Literal["auto", "1s", "1min"] = "auto", max_points: int = Query(0, ge=0, le=5000)):
        """`max_points` > 0: cada serie se reduce a ese máximo para dibujar (mínimo y máximo por tramo)."""
        start_ms, end_ms = parse_range(start, end, 15 * 60_000, 31 * DAY_MS)
        addrs = parse_addresses(addresses)
        chosen = resolution if resolution != "auto" else ("1s" if end_ms - start_ms <= 2 * HOUR_MS else "1min")
        tl = timeline(addrs or [])
        if chosen == "1s":
            if end_ms - start_ms > 6 * HOUR_MS:
                raise ApiFailure(422, "invalid_input", "Resolución de 1 s hasta 6 h; use 1min para intervalos mayores")
            data = unpack(range_samples(reader, cfg.archive_dir, start_ms, end_ms, addrs, limit=80_000))
            items = [slim(r) for r in annotate_samples(data["items"], tl)]
            items, bucket_ms, reduced = decimate(items, time_key="t_ms", value_key="measurement", valid_key="valid",
                                                 start_ms=start_ms, end_ms=end_ms, max_points=max_points,
                                                 base_step_ms=1000)
            return {"resolution": "1s", "start_ms": start_ms, "end_ms": end_ms, "items": items,
                    "truncated": data["truncated"], "bucket_ms": bucket_ms, "decimated": reduced}
        items = [r for page in iter_minutes(reader, start_ms, end_ms, addrs) for r in page]
        annotate_minutes(items, tl)
        for r in items:
            r["qc_failed"] = [t for t in TEST_LABELS if r["qc"].get(t) == FAIL]
            r["qc"] = r["qc"]["summary"]
        items, bucket_ms, reduced = decimate(items, time_key="minute_ms", value_key="mean_wm2", valid_key="n_valid",
                                             start_ms=start_ms, end_ms=end_ms, max_points=max_points,
                                             base_step_ms=60_000, offset_ms=30_000)
        return {"resolution": "1min", "start_ms": start_ms, "end_ms": end_ms, "items": items, "truncated": False,
                "bucket_ms": bucket_ms, "decimated": reduced}

    @app.get("/api/minutes")
    def range_minutes(start: str | None = None, end: str | None = None, addresses: str | None = None):
        start_ms, end_ms = parse_range(start, end, 7 * DAY_MS, 92 * DAY_MS)
        addrs = parse_addresses(addresses)
        items = [r for page in iter_minutes(reader, start_ms, end_ms, addrs) for r in page]
        annotate_minutes(items, timeline(addrs or []))
        return {"start_ms": start_ms, "end_ms": end_ms, "items": [
            {"minute_ms": r["minute_ms"], "address": r["address"], "role": r["role"], "label": r["label"],
             "mean_wm2": r["mean_wm2"], "n_valid": r["n_valid"], "complete": r["complete"],
             "qc": r["qc"]["summary"]} for r in items]}

    @app.get("/api/instruments")
    def instruments():
        run = latest_run()
        tl = timeline()
        current = tl.current(now_ms())
        if run is None:
            return {"run": None, "items": [], "config": current.model_dump()}
        meta = run["metadata"]
        types = sensor_types(run)
        latest = {s["address"]: s for s in annotate_samples(unpack(reader.latest(run["id"])), tl)}
        per_sensor = (run["status"].get("per_sensor") or {})
        items = []
        for address in sorted(set(types) | set(latest)):
            inst = current.instrument(address)
            info = (meta.get("sensors") or {}).get(str(address))
            if info and "serial_label" not in info:
                # §15.10: solo la combinación lote + número de serie es única.
                info = {**info, "serial_label": f"{info.get('batch_year', 0):02d}{info.get('serial_number', 0):04d}"}
            items.append({
                "address": address, "sensor_type": types.get(address, latest.get(address, {}).get("sensor_type")),
                "info": info,
                "stats": per_sensor.get(str(address)) or (meta.get("per_sensor") or {}).get(str(address)),
                "latest": latest.get(address), "config": inst.model_dump() if inst else None,
                "role": latest[address]["role"] if address in latest else None,
            })
        return {"run": {"id": run["id"], "state": run["state"], "started_utc": run["started_utc"],
                        "simulated": meta.get("simulated"), "bus": meta.get("bus"),
                        "label": (meta.get("config") or {}).get("label")},
                "items": items, "config": current.model_dump()}

    @app.get("/api/timeline")
    def timeline_view(start: str | None = None, end: str | None = None,
                      sources: str = "adquisicion,bitacora,configuracion", limit: int = Query(1000, ge=1, le=10000)):
        start_ms, end_ms = parse_range(start, end, 7 * DAY_MS, 366 * DAY_MS)
        wanted = {s.strip() for s in sources.split(",")}
        items = []
        if "adquisicion" in wanted:
            events = reader.events(start_ms, end_ms, limit)
            if isinstance(events, Err) and events.error.code != "database_unavailable":
                unpack(events)
            for e in [] if isinstance(events, Err) else events.value:
                items.append({"t_ms": e["t_ms"], "time_utc": e["time_utc"], "source": "adquisicion", "kind": e["kind"],
                              "severity": e["severity"], "address": e["address"], "run_id": e["run_id"],
                              "title": describe(e["kind"], e["detail"], e["address"]), "detail": e["detail"]})
        if "bitacora" in wanted:
            for e in unpack(observatory.logbook(start_ms, end_ms, True, limit)):
                items.append({"t_ms": e["t_ms"], "time_utc": e["time_utc"], "source": "bitacora", "kind": e["category"],
                              "severity": "info", "address": None, "title": e["category_label"], "entry": e})
        if "configuracion" in wanted:
            for v in unpack(observatory.versions()):
                if start_ms <= v["valid_from_ms"] < end_ms:
                    items.append({"t_ms": v["valid_from_ms"], "time_utc": v["valid_from"], "source": "configuracion",
                                  "kind": "config_version", "severity": "info", "address": None,
                                  "title": f"Configuración v{v['id']} ({v['author']})", "version": v})
        items.sort(key=lambda i: i["t_ms"], reverse=True)
        return {"start_ms": start_ms, "end_ms": end_ms, "items": items[:limit]}

    @app.get("/api/clock")
    def clock(limit: int = Query(50, ge=1, le=1000)):
        events = reader.latest_events(("clock_check", "clock_unavailable"), limit)
        if isinstance(events, Err) and events.error.code == "database_unavailable":
            return {"items": []}
        return {"items": unpack(events), "max_offset_s": cfg.clock_max_offset_s, "check_every_s": cfg.clock_check_s}

    @app.get("/api/archive")
    def archive_status():
        return {"days": sorted(unpack(reader.manifest()).values(), key=lambda d: d["day"]),
                "bounds": unpack(reader.bounds()), "archive_dir": str(cfg.archive_dir)}

    # ------------------------------------------------- base del observatorio
    @app.get("/api/observatory/config")
    def get_config():
        versions = unpack(observatory.versions())
        types = sensor_types()
        tl = ConfigTimeline.build(versions, types)
        now = now_ms()
        return {"current": tl.current(now).model_dump(), "current_version": tl.version_id(now),
                "versions": list(reversed(versions)), "sensor_types": {str(a): t for a, t in types.items()},
                "categories": CATEGORIES, "qc_defaults": QCConfig().model_dump(), "qc_tests": TEST_LABELS}

    @app.post("/api/observatory/config")
    def post_config(change: ConfigChange):
        now = now_ms()
        try:
            warnings, valid_from = validate_change(change, sensor_types(), now)
        except ValueError as exc:
            raise ApiFailure(422, "invalid_input", str(exc))
        previous = timeline([i.address for i in change.config.instruments]).at(valid_from)
        version = unpack(observatory.add_version(change, previous, valid_from))
        return {"version": version, "warnings": warnings}

    @app.get("/api/logbook")
    def get_logbook(start: str | None = None, end: str | None = None, include_voided: bool = True,
                    limit: int = Query(500, ge=1, le=5000)):
        start_ms, end_ms = parse_range(start, end, 30 * DAY_MS, 3660 * DAY_MS)
        return {"items": unpack(observatory.logbook(start_ms, end_ms, include_voided, limit)), "categories": CATEGORIES}

    @app.post("/api/logbook")
    def post_logbook(entry: LogbookEntryIn):
        return unpack(observatory.add_entry(entry, now_ms()))

    @app.post("/api/logbook/{entry_id}/void")
    def void_logbook(entry_id: int, body: VoidIn):
        return unpack(observatory.void_entry(entry_id, body, now_ms()))

    # --------------------------------------------- exportación y figuras
    def export_query(start, end, addresses, resolution, invalid) -> dict:
        start_ms, end_ms = parse_range(start, end, DAY_MS, MAX_SPAN_MS[resolution])
        addrs = parse_addresses(addresses)
        unpack(reader.runs(1))  # BD disponible antes de empezar a transmitir
        return {"start_ms": start_ms, "end_ms": end_ms, "start_utc": iso_ms(start_ms), "end_utc": iso_ms(end_ms),
                "addresses": addrs, "resolution": resolution, "include_invalid": invalid == "include"}

    @app.get("/api/export/estimate")
    def export_estimate(start: str | None = None, end: str | None = None, addresses: str | None = None,
                        resolution: Literal["1s", "1min"] = "1min"):
        q = export_query(start, end, addresses, resolution, "include")
        if resolution == "1min":
            rows = unpack(reader.count_minutes(q["start_ms"], q["end_ms"], q["addresses"]))
        else:
            rows = unpack(reader.count_range(q["start_ms"], q["end_ms"], q["addresses"]))
            for day in unpack(reader.manifest()).values():
                if day["pruned_ms"] is not None:
                    from .archive import day_range
                    d0, d1 = day_range(day["day"])
                    if d0 < q["end_ms"] and d1 > q["start_ms"]:
                        rows += round(day["rows"] * (min(d1, q["end_ms"]) - max(d0, q["start_ms"])) / (d1 - d0))
        per_row = 330 if resolution == "1s" else 260
        return {"rows": rows, "approx_csv_bytes": rows * per_row, "max_span_days": MAX_SPAN_MS[resolution] // DAY_MS}

    @app.get("/api/export")
    async def export(start: str | None = None, end: str | None = None, addresses: str | None = None,
                     resolution: Literal["1s", "1min"] = "1min", invalid: Literal["include", "exclude"] = "include",
                     format: Literal["csv", "zip"] = "csv"):
        q = await run_in_threadpool(export_query, start, end, addresses, resolution, invalid)
        tl = await run_in_threadpool(timeline, q["addresses"] or [])
        stem = file_stem(q)
        if format == "csv":
            def generate():
                try:
                    yield from iter_csv(reader, cfg.archive_dir, tl, q["start_ms"], q["end_ms"], q["addresses"],
                                        resolution, q["include_invalid"])
                except StorageFailure as exc:
                    yield f"# ERROR: exportación incompleta ({exc.error.code}: {exc.error.message})\n"
            return StreamingResponse(generate(), media_type="text/csv; charset=utf-8",
                                     headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
        def make_zip():
            logbook = unpack(observatory.logbook(q["start_ms"], q["end_ms"], True, 100_000))
            events = reader.events(q["start_ms"], q["end_ms"], 100_000)
            return build_zip(reader, cfg.archive_dir, tl, unpack(observatory.versions()), logbook,
                             [] if isinstance(events, Err) else events.value, q)
        spool = await run_in_threadpool(make_zip)
        return StreamingResponse(iter(lambda: spool.read(1 << 16), b""), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}.zip"'})

    @app.get("/api/figure")
    async def figure(start: str | None = None, end: str | None = None, addresses: str | None = None,
                     resolution: Literal["auto", "1s", "1min"] = "auto"):
        def render():
            start_ms, end_ms = parse_range(start, end, DAY_MS, 31 * DAY_MS)
            addrs = parse_addresses(addresses)
            chosen = resolution if resolution != "auto" else ("1s" if end_ms - start_ms <= 6 * HOUR_MS else "1min")
            if chosen == "1s" and end_ms - start_ms > 6 * HOUR_MS:
                raise ApiFailure(422, "invalid_input", "Figura de 1 s hasta 6 h")
            tl = timeline(addrs or [])
            if chosen == "1s":
                rows = unpack(range_samples(reader, cfg.archive_dir, start_ms, end_ms, addrs, 120_000))["items"]
                annotate_samples(rows, tl)
            else:
                rows = annotate_minutes([r for p in iter_minutes(reader, start_ms, end_ms, addrs) for r in p], tl)
            logbook = unpack(observatory.logbook(start_ms, end_ms, False, 500))
            runs = reader.runs_in_range(start_ms, end_ms)
            simulated = any(r["metadata"].get("simulated") for r in ([] if isinstance(runs, Err) else runs.value))
            footer = (f"{iso_ms(start_ms)} – {iso_ms(end_ms)} UTC · instrumentos: "
                      f"{', '.join(f'#{a}' for a in addrs) if addrs else 'todos'} · generado "
                      f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} · smp22x {__version__} · "
                      "valores sin corrección adicional · QC: Long y Dutton (2010) y pruebas del proyecto")
            from .plotting import range_figure
            with figure_lock:
                return range_figure(rows, chosen, logbook, footer, simulated, start_ms, end_ms), chosen, start_ms, end_ms
        png, chosen, start_ms, end_ms = await run_in_threadpool(render)
        stem = file_stem({"start_ms": start_ms, "end_ms": end_ms, "resolution": chosen})
        return Response(png, media_type="image/png", headers={"Content-Disposition": f'inline; filename="{stem}.png"'})

    @app.get("/api/{unknown:path}")
    def missing_api(unknown: str):
        raise ApiFailure(404, "not_found", "Endpoint no encontrado")

    if cfg.references.is_dir():
        app.mount("/manuales", StaticFiles(directory=cfg.references), name="manuales")
    if cfg.frontend.is_dir():
        # adapter-static genera una carpeta por ruta (trailingSlash: always).
        app.mount("/", FrontendFiles(directory=cfg.frontend, html=True), name="frontend")
    else:
        @app.get("/")
        def build_missing():
            return JSONResponse({"message": "Compila frontend: bun run --cwd frontend build", "api": "/docs"}, status_code=503)
    return app

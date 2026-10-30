"""Read-only web process. Never opens a serial port or starts acquisition."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import Settings, settings
from .metrics import age_s, comparison
from .result import Err
from .storage import Reader
from .tracing import emit, trace_id, trace_path


class ApiFailure(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def unpack(result):
    if isinstance(result, Err):
        e = result.error
        raise ApiFailure(404 if e.code == "run_not_found" else 503, e.code, e.message, e.retryable)
    return result.value


def utc_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    except ValueError:
        raise ApiFailure(422, "invalid_input", "Fecha ISO 8601 con zona requerida")


def event(name: str, data: dict, cursor: int | None = None) -> str:
    return (f"id: {cursor}\n" if cursor is not None else "") + f"event: {name}\ndata: {json.dumps(data, allow_nan=False)}\n\n"


def create_app(config: Settings | None = None) -> FastAPI:
    cfg = config or settings()
    reader = Reader(cfg.db)
    app = FastAPI(title="Radiometría local", version="1.0")

    @app.middleware("http")
    async def trace_request(request: Request, call_next):
        token = trace_id.set(uuid.uuid4().hex)
        path = trace_path.set((f"HTTP {request.method} {request.url.path}",))
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id.get()
            emit("HTTP", "FAILED" if response.status_code >= 400 else "OK",
                 (time.perf_counter() - started) * 1000, http_status=response.status_code)
            return response
        except Exception:
            import traceback
            emit("HTTP", "FAILED", (time.perf_counter() - started) * 1000,
                 error="internal_error", traceback=traceback.format_exc())
            return JSONResponse({"ok": False, "error": {"code": "internal_error", "message": "Error interno",
                                "retryable": False}, "trace_id": trace_id.get()}, status_code=500,
                                headers={"X-Trace-ID": trace_id.get()})
        finally:
            trace_path.reset(path)
            trace_id.reset(token)

    @app.exception_handler(ApiFailure)
    async def failure_handler(request, exc):
        return JSONResponse({"ok": False, "error": {"code": exc.code, "message": exc.message,
                            "retryable": exc.retryable}, "trace_id": trace_id.get()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        return await failure_handler(request, ApiFailure(422, "invalid_input", "Parámetros fuera de rango o inválidos"))

    @app.get("/api/health")
    def health():
        return {"ok": True, "database_ready": not isinstance(reader.runs(1), Err)}

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
        start, end = utc_filter(start), utc_filter(end)
        if start and end and start > end:
            raise ApiFailure(422, "invalid_input", "start debe ser anterior a end")
        return unpack(reader.samples(run_id, after_id, limit, address, start, end, recent))

    @app.get("/api/runs/{run_id}/minutes")
    def minutes(run_id: str, limit: int = Query(1000, ge=1, le=5000)):
        unpack(reader.run(run_id))
        return {"items": unpack(reader.minutes(run_id, limit))}

    @app.get("/api/runs/{run_id}/status")
    def status(run_id: str):
        run = unpack(reader.run(run_id))
        latest = unpack(reader.latest(run_id))
        for s in latest:
            s["age_s"] = age_s(s["time_utc"])
            s["stale"] = s["age_s"] > cfg.stale_s
        heartbeat = run["status"].get("heartbeat_utc", run["started_utc"])
        return {"run": run, "latest": latest, "comparison": comparison(latest),
                "heartbeat_age_s": age_s(heartbeat), "stale_s": cfg.stale_s,
                "acquisition_stale": run["state"] == "running" and age_s(heartbeat) > cfg.stale_s}

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

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                result = await run_in_threadpool(reader.samples, run_id, cursor, 1000)
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

    @app.get("/api/{unknown:path}")
    def missing_api(unknown: str):
        raise ApiFailure(404, "not_found", "Endpoint no encontrado")

    if cfg.frontend.is_dir():
        # adapter-static provides index.html; there is one dashboard route.
        app.mount("/", StaticFiles(directory=cfg.frontend, html=True), name="frontend")
    else:
        @app.get("/")
        def build_missing():
            return JSONResponse({"message": "Compila frontend: bun run --cwd frontend build", "api": "/docs"}, status_code=503)
    return app

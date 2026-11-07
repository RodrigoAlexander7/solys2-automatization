"""JSON traces with explicit, allowlisted context and nested operation paths."""
from __future__ import annotations

import functools
import inspect
import json
import logging
import time
import uuid
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable, ParamSpec, TypeVar

from .result import Err

P = ParamSpec("P")
T = TypeVar("T")
trace_id: ContextVar[str] = ContextVar("trace_id", default="")
trace_path: ContextVar[tuple[str, ...]] = ContextVar("trace_path", default=())
logger = logging.getLogger("smp22x.trace")


class ConsoleFilter(logging.Filter):
    """En consola, solo lo inesperado: excepciones con traceback y HTTP 5xx.

    Los fallos esperados (timeouts, puerto, BD ocupada) ya se ven en la línea de
    ciclo, en los eventos o en el mensaje de error del comando.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        p = getattr(record, "payload", None) or {}
        return bool(p.get("traceback")) or (p.get("operation") == "HTTP" and (p.get("http_status") or 0) >= 500)


class ConsoleFormatter(logging.Formatter):
    def __init__(self, log_file: str = "") -> None:
        super().__init__()
        self.log_file = log_file

    def format(self, record: logging.LogRecord) -> str:
        p = getattr(record, "payload", None) or {}
        where = p.get("path") or p.get("operation", "")
        message = p.get("message") or p.get("error") or (f"HTTP {p['http_status']}" if p.get("http_status") else "")
        detail = f"; detalle en {self.log_file}" if self.log_file else ""
        return (f"{datetime.now().strftime('%H:%M:%S')}  ERROR  {where}: {message} "
                f"(traza {str(p.get('trace_id', ''))[:8]}{detail})")


def configure_logging(level: str = "INFO", fmt: str = "json", log_file: str | None = None) -> None:
    """json: todas las trazas a stderr (formato anterior).
    texto: JSON al archivo rotativo `log_file`; en consola solo errores inesperados."""
    from logging.handlers import RotatingFileHandler
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    if fmt == "texto":
        console = logging.StreamHandler()
        console.setLevel(logging.ERROR)
        console.addFilter(ConsoleFilter())
        console.setFormatter(ConsoleFormatter(log_file or ""))
        logger.addHandler(console)
        if log_file:
            from pathlib import Path
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            file.setFormatter(logging.Formatter("%(message)s"))
            file.setLevel(level.upper())
            logger.addHandler(file)
        # Errores inesperados deben llegar a la consola aunque el nivel del archivo sea más alto.
        logger.setLevel(min(logging.getLevelName(level.upper()), logging.ERROR))
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(level.upper())
    logger.propagate = False


def emit(operation: str, status: str, duration_ms: float = 0, **context) -> None:
    level = logging.ERROR if status == "FAILED" else logging.INFO
    if not logger.isEnabledFor(level):
        return  # no serializar trazas que nadie va a leer (CPU en la mini-PC)
    payload = dict(time_utc=datetime.now(timezone.utc).isoformat(), operation=operation,
                   path=" -> ".join(trace_path.get() or (operation,)),
                   trace_id=trace_id.get(), status=status, duration_ms=round(duration_ms, 3),
                   **context)
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str), extra={"payload": payload})


def traced(operation: str, *, fields: tuple[str, ...] = (), expected: tuple[type[Exception], ...] = ()):
    """Sync boundary decorator. Unexpected errors retain traceback and propagate."""
    def decorate(fn: Callable[P, T]) -> Callable[P, T]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            root = trace_id.set(trace_id.get() or uuid.uuid4().hex)
            path = trace_path.set((*trace_path.get(), operation))
            start = time.perf_counter()
            bound = signature.bind(*args, **kwargs)
            context = {k: bound.arguments[k] for k in fields if k in bound.arguments}
            try:
                value = fn(*args, **kwargs)
                error = value.error if isinstance(value, Err) else None
                failed_sample = getattr(value, "ok", True) is False and error is None
                emit(operation, "FAILED" if error or failed_sample else "OK",
                     (time.perf_counter() - start) * 1000,
                     **context, **({"error": error.code, "message": error.message} if error else
                                  {"error": getattr(value, "error_code", "sample_failure"),
                                   "message": str(getattr(value, "error", ""))} if failed_sample else {}))
                return value
            except Exception as exc:
                emit(operation, "FAILED", (time.perf_counter() - start) * 1000,
                     **context, error=type(exc).__name__, message=str(exc),
                     **({} if isinstance(exc, expected) else {"traceback": traceback.format_exc()}))
                raise
            finally:
                trace_path.reset(path)
                trace_id.reset(root)
        return wrapped
    return decorate

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


def configure_logging(level: str = "INFO") -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def emit(operation: str, status: str, duration_ms: float = 0, **context) -> None:
    payload = dict(time_utc=datetime.now(timezone.utc).isoformat(), operation=operation,
                   path=" -> ".join(trace_path.get() or (operation,)),
                   trace_id=trace_id.get(), status=status, duration_ms=round(duration_ms, 3),
                   **context)
    logger.log(logging.ERROR if status == "FAILED" else logging.INFO,
               json.dumps(payload, ensure_ascii=False, default=str))


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

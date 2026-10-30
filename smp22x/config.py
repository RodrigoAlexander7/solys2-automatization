"""Shared local configuration; env > .env, CLI > both. No import side effects."""
from dataclasses import dataclass
import math
import os
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    db: Path = Path("data/acquisition.sqlite3")
    host: str = "127.0.0.1"
    port: int = 8050
    poll_s: float = 1.0
    stale_s: float = 5.0
    frontend: Path = Path("frontend/build")
    log_level: str = "INFO"


def environment(path: Path = Path(".env")) -> dict[str, str]:
    return {k: v for k, v in {**dotenv_values(path), **os.environ}.items() if v is not None}


def settings(env: dict[str, str] | None = None) -> Settings:
    e = environment() if env is None else env
    port = int(e.get("SMP_API_PORT", "8050"))
    poll = float(e.get("SMP_POLL_S", "1"))
    stale = float(e.get("SMP_STALE_S", "5"))
    if not 1 <= port <= 65535 or not math.isfinite(poll) or not 0.1 <= poll <= 60:
        raise ValueError("SMP_API_PORT o SMP_POLL_S fuera de rango")
    if not math.isfinite(stale) or stale < poll:
        raise ValueError("SMP_STALE_S debe ser finito y >= SMP_POLL_S")
    level = e.get("SMP_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("SMP_LOG_LEVEL no válido")
    return Settings(Path(e.get("SMP_DB_PATH", "data/acquisition.sqlite3")),
                    e.get("SMP_API_HOST", "127.0.0.1"), port, poll, stale,
                    Path(e.get("SMP_FRONTEND_DIR", "frontend/build")), level)

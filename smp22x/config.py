"""Shared local configuration; env > .env, CLI > both. No import side effects."""
from dataclasses import dataclass
import ipaddress
import math
import os
from pathlib import Path

from dotenv import dotenv_values

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class Settings:
    db: Path = Path("data/acquisition.sqlite3")
    host: str = "127.0.0.1"
    port: int = 8050
    poll_s: float = 1.0
    stale_s: float = 5.0
    frontend: Path = Path("frontend/build")
    log_level: str = "INFO"
    observatory: Path = Path("data/observatory.sqlite3")
    archive_dir: Path = Path("data/archive")
    references: Path = Path("references")
    allowed_hosts: tuple[str, ...] = LOCAL_HOSTS
    container_loopback: bool = False
    clock_check_s: float = 600.0
    clock_max_offset_s: float = 1.0


def environment(path: Path = Path(".env")) -> dict[str, str]:
    return {k: v for k, v in {**dotenv_values(path), **os.environ}.items() if v is not None}


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _flag(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered not in {"0", "1", "true", "false", "yes", "no", ""}:
        raise ValueError(f"Valor booleano no válido: {value!r}")
    return lowered in {"1", "true", "yes"}


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
    clock_check = float(e.get("SMP_CLOCK_CHECK_S", "600"))
    max_offset = float(e.get("SMP_CLOCK_MAX_OFFSET_S", "1"))
    if not (math.isfinite(clock_check) and clock_check >= 10 and math.isfinite(max_offset) and max_offset > 0):
        raise ValueError("SMP_CLOCK_CHECK_S >= 10 y SMP_CLOCK_MAX_OFFSET_S > 0")
    extra = tuple(h.strip().lower() for h in e.get("SMP_ALLOWED_HOSTS", "").split(",") if h.strip())
    db = Path(e.get("SMP_DB_PATH", "data/acquisition.sqlite3"))
    return Settings(db, e.get("SMP_API_HOST", "127.0.0.1"), port, poll, stale,
                    Path(e.get("SMP_FRONTEND_DIR", "frontend/build")), level,
                    Path(e.get("SMP_OBSERVATORY_DB", str(db.with_name("observatory.sqlite3")))),
                    Path(e.get("SMP_ARCHIVE_DIR", str(db.with_name("archive")))),
                    Path(e.get("SMP_REFERENCES_DIR", "references")),
                    LOCAL_HOSTS + extra, _flag(e.get("SMP_CONTAINER_LOOPBACK", "0")),
                    clock_check, max_offset)

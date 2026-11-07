"""Comprobación del reloj del PC con SNTP (RFC 4330).

Cada muestra lleva la hora del PC. Este módulo mide cuánto se desvía ese reloj
de un servidor NTP; no lo corrige ni modifica marcas de tiempo. La consulta se
hace en un hilo aparte para no retrasar el ciclo de lectura del bus.
"""
from __future__ import annotations

import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .events import INFO, WARNING, Event

NTP_EPOCH_OFFSET = 2_208_988_800  # segundos entre 1900-01-01 y 1970-01-01
PACKET = struct.Struct("!BBbb11I")


@dataclass(frozen=True)
class ClockCheck:
    t_ms: int
    server: str
    ok: bool
    offset_s: float | None = None   # reloj del servidor − reloj del PC
    delay_s: float | None = None
    stratum: int | None = None
    error: str = ""


def _to_ntp(t: float) -> tuple[int, int]:
    seconds = t + NTP_EPOCH_OFFSET
    whole = int(seconds)
    return whole, int((seconds - whole) * 2 ** 32) & 0xFFFFFFFF


def _from_ntp(whole: int, fraction: int) -> float:
    return whole - NTP_EPOCH_OFFSET + fraction / 2 ** 32


def build_request(t1: float) -> bytes:
    words = [0] * 11
    words[9], words[10] = _to_ntp(t1)  # transmit timestamp: el servidor lo devuelve como origen
    return PACKET.pack(0x23, 0, 0, 0, *words)  # LI=0, versión 4, modo 3 (cliente)


def parse_response(data: bytes, t1: float, t4: float) -> tuple[float, float, int]:
    """Devuelve (offset, retardo, estrato). Lanza ValueError si la respuesta no es válida."""
    if len(data) < PACKET.size:
        raise ValueError("respuesta NTP demasiado corta")
    first, stratum, _poll, _precision, *words = PACKET.unpack(data[:PACKET.size])
    mode, version = first & 0x7, (first >> 3) & 0x7
    if mode != 4 or version not in (3, 4):
        raise ValueError(f"respuesta NTP de modo {mode} / versión {version}")
    if not 1 <= stratum <= 15:
        raise ValueError(f"servidor NTP no sincronizado (estrato {stratum})")
    originate = _from_ntp(words[5], words[6])
    if abs(originate - t1) > 1e-5:
        raise ValueError("la respuesta NTP no corresponde a la petición")
    t2, t3 = _from_ntp(words[7], words[8]), _from_ntp(words[9], words[10])
    offset = ((t2 - t1) + (t3 - t4)) / 2
    delay = (t4 - t1) - (t3 - t2)
    return offset, delay, stratum


def sntp_check(server: str, port: int = 123, timeout: float = 2.0,
               clock: Callable[[], float] = time.time) -> ClockCheck:
    t1 = clock()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(build_request(t1), (server, port))
            data, _ = sock.recvfrom(512)
        t4 = clock()
        offset, delay, stratum = parse_response(data, t1, t4)
        return ClockCheck(int(t4 * 1000), server, True, offset, delay, stratum)
    except (OSError, ValueError) as exc:
        return ClockCheck(int(clock() * 1000), server, False, error=f"{type(exc).__name__}: {exc}")


def clock_event(check: ClockCheck, max_offset_s: float) -> Event:
    if not check.ok:
        return Event(check.t_ms, "clock_unavailable", WARNING, None,
                     {"server": check.server, "error": check.error})
    severity = WARNING if abs(check.offset_s) > max_offset_s else INFO
    return Event(check.t_ms, "clock_check", severity, None, {
        "server": check.server, "offset_s": round(check.offset_s, 4), "delay_s": round(check.delay_s, 4),
        "stratum": check.stratum, "max_offset_s": max_offset_s})


def clock_status(check: ClockCheck, max_offset_s: float) -> dict:
    return {"t_ms": check.t_ms, "server": check.server, "ok": check.ok, "offset_s": check.offset_s,
            "delay_s": check.delay_s, "error": check.error,
            "within_limit": check.ok and abs(check.offset_s) <= max_offset_s, "max_offset_s": max_offset_s}


class ClockMonitor(threading.Thread):
    """Hilo que consulta el servidor cada `every_s` y deja resultados en una cola."""

    def __init__(self, server: str, every_s: float,
                 check: Callable[[str], ClockCheck] = sntp_check) -> None:
        super().__init__(name="clock-monitor", daemon=True)
        self.server, self.every_s, self.check = server, every_s, check
        self.results: queue.Queue[ClockCheck] = queue.Queue()
        self.stopping = threading.Event()

    def run(self) -> None:
        while not self.stopping.is_set():
            self.results.put(self.check(self.server))
            self.stopping.wait(self.every_s)

    def drain(self) -> list[ClockCheck]:
        items = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items

    def stop(self) -> None:
        self.stopping.set()

"""Salida de terminal legible para quien opera la adquisición (R30).

Una línea por ciclo (o un resumen cada N s) y los eventos como frases. Las
trazas JSON completas no se pierden: van a un archivo rotativo (tracing.py).
"""
from __future__ import annotations

import statistics
from datetime import datetime

from . import registers as reg
from .events import Event, describe, from_ms
from .sensor import Sample

LEVELS = {"info": "INFO ", "warning": "AVISO", "error": "ERROR"}


def num(value: float | None, width: int = 7, digits: int = 1) -> str:
    """Número con coma decimal y ancho fijo; ausente como '---'."""
    if value is None:
        return f"{'---':>{width}}"
    return f"{value:{width}.{digits}f}".replace(".", ",")


def clock(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).strftime("%H:%M:%S")


def _name(s: Sample) -> str:
    return f"#{s.address} Ld↓" if s.sensor_type == reg.PYRGEOMETER else f"#{s.address}"


def cycle_line(samples: list[Sample], cycle_ms: float, moment: datetime | None = None) -> str:
    parts, notes = [], []
    for s in samples:
        if s.valid:
            parts.append(f"{_name(s)} {num(s.measurement)}")
        elif s.ok:
            parts.append(f"{_name(s)} {'INV':>7}")
            notes.append(f"#{s.address} inválida ({', '.join(s.status_names) or f'modo {s.mode}'})")
        else:
            parts.append(f"{_name(s)} {'---':>7}")
            notes.append(f"#{s.address} sin respuesta")
    line = f"{clock(moment)}  " + "   ".join(parts) + f"  W/m²   ciclo {num(cycle_ms, 4, 0)} ms"
    return line + ("   " + "; ".join(notes) if notes else "")


def event_line(event: Event) -> str:
    moment = from_ms(event.t_ms).astimezone()
    return f"{clock(moment)}  {LEVELS.get(event.severity, 'INFO ')}  {describe(event.kind, event.detail, event.address)}"


class Summary:
    """Acumula ciclos y produce una línea cada `every_s` segundos."""

    def __init__(self, every_s: float) -> None:
        self.every_s = every_s
        self.reset()

    def reset(self) -> None:
        self.values: dict[str, list[float]] = {}
        self.counts: dict[str, list[int]] = {}
        self.cycles: list[float] = []
        self.started: float | None = None

    def add(self, samples: list[Sample], cycle_ms: float, now: float) -> str | None:
        if self.started is None:
            self.started = now
        self.cycles.append(cycle_ms)
        for s in samples:
            name = _name(s)
            self.values.setdefault(name, [])
            valid, total = self.counts.setdefault(name, [0, 0])
            self.counts[name] = [valid + int(s.valid), total + 1]
            if s.valid:
                self.values[name].append(s.measurement)
        if now - self.started < self.every_s:
            return None
        parts = [f"{name} {num(statistics.fmean(v) if v else None)}" for name, v in self.values.items()]
        valid = sum(c[0] for c in self.counts.values())
        total = sum(c[1] for c in self.counts.values())
        line = (f"{clock()}  media {self.every_s:g} s  " + "   ".join(parts) +
                f"  W/m²   válidas {valid}/{total}   ciclo medio {num(statistics.fmean(self.cycles), 4, 0)} ms")
        self.reset()
        return line

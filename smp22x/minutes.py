"""Promedios de 1 minuto por sensor (recomendación §9.2 del manual).

La magnitud que se promedia depende del tipo de sensor: irradiancia global en
los piranómetros y onda larga descendente Ld↓ en el pirgeómetro. Solo entran
muestras válidas; `complete` exige al menos el 80 % de las esperadas.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from . import registers as reg
from .events import from_ms, to_ms
from .sensor import Sample

MINUTE_COLUMNS = [
    "minute_local", "minute_utc", "address", "sensor_type", "n_samples", "n_valid",
    "mean_wm2", "min_wm2", "max_wm2", "std_wm2", "net_mean_wm2", "body_temp_c_mean", "complete",
]
COMPLETE_FRACTION = 0.8


def fmt(value: float | None, digits: int = 1) -> str:
    return "" if value is None else f"{value:.{digits}f}"


@dataclass(frozen=True)
class MinuteRecord:
    minute_ms: int
    address: int
    sensor_type: str
    n_samples: int
    n_valid: int
    mean: float | None
    min: float | None
    max: float | None
    std: float | None
    net_mean: float | None
    body_temp_mean: float | None
    complete: bool

    def csv_row(self) -> list:
        minute = from_ms(self.minute_ms)
        return [
            minute.astimezone().isoformat(timespec="seconds"),
            minute.isoformat(timespec="seconds"),
            self.address, self.sensor_type, self.n_samples, self.n_valid,
            fmt(self.mean, 2), fmt(self.min), fmt(self.max), fmt(self.std, 2),
            fmt(self.net_mean, 2), fmt(self.body_temp_mean, 2), int(self.complete),
        ]


class MinuteAggregator:
    """Acumula muestras y devuelve los minutos que se cierran al llegar el siguiente."""

    def __init__(self, interval_s: float) -> None:
        self.expected = 60.0 / interval_s
        self.buckets: dict[int, dict] = {}

    def add(self, s: Sample) -> list[MinuteRecord]:
        minute = to_ms(s.time_utc.replace(second=0, microsecond=0))
        bucket = self.buckets.get(s.address)
        closed: list[MinuteRecord] = []
        if bucket is not None and bucket["minute"] != minute:
            closed.append(self._record(s.address, bucket))
            bucket = None
        if bucket is None:
            bucket = {"minute": minute, "type": s.sensor_type, "n": 0, "val": [], "net": [], "temp": []}
            self.buckets[s.address] = bucket
        bucket["n"] += 1
        if s.valid:
            bucket["val"].append(s.measurement)
            if s.sensor_type == reg.PYRGEOMETER and s.irradiance is not None:
                bucket["net"].append(s.irradiance)
            if s.body_temp_c is not None:
                bucket["temp"].append(s.body_temp_c)
        return closed

    def _record(self, address: int, b: dict) -> MinuteRecord:
        val = b["val"]
        return MinuteRecord(
            minute_ms=b["minute"], address=address, sensor_type=b["type"], n_samples=b["n"],
            n_valid=len(val),
            mean=statistics.fmean(val) if val else None,
            min=min(val) if val else None,
            max=max(val) if val else None,
            std=statistics.pstdev(val) if len(val) > 1 else None,
            net_mean=statistics.fmean(b["net"]) if b["net"] else None,
            body_temp_mean=statistics.fmean(b["temp"]) if b["temp"] else None,
            complete=len(val) >= COMPLETE_FRACTION * self.expected,
        )

    def flush(self) -> list[MinuteRecord]:
        records = [self._record(a, b) for a, b in sorted(self.buckets.items())]
        self.buckets.clear()
        return records

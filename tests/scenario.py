"""Escenarios simulados con reloj controlado: recorren días enteros en segundos.

Pasan por la misma cadena que el hardware: registros Modbus simulados →
SMPSensor (conversión) → Writer (SQLite) → Reader → interpretación. Solo cambia
la marca de tiempo, que toma el reloj del escenario en lugar del reloj del PC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from smp22x import registers as reg
from smp22x.analysis import ConfigTimeline, annotate_minutes, annotate_samples
from smp22x.events import EventTracker
from smp22x.minutes import MinuteAggregator
from smp22x.transport import PortError
from smp22x.result import require
from smp22x.sensor import SMPSensor
from smp22x.simulator import DEFAULT_SITE, Faults, ManualClock, SimulatedBus
from smp22x.storage import Reader, Writer

SITE = {"latitude": DEFAULT_SITE[0], "longitude": DEFAULT_SITE[1], "altitude_m": DEFAULT_SITE[2]}
# 21 de junio de 2026 en el sitio de referencia: mediodía solar ≈ 19:00 UTC.
SUMMER_DAY = datetime(2026, 6, 21, tzinfo=timezone.utc)


def simulate(db: Path, *, start: datetime = SUMMER_DAY, hours: float = 24, step_s: float = 60,
             addresses=(2, 3, 4), pyrgeometers=(2,), shaded=(), tracker_active: bool = True,
             faults: dict[int, Faults] | None = None, seed: int = 1, cloud_mean_gap_s: float | None = None,
             run_id: str = "escenario", writer: Writer | None = None) -> dict:
    clock = ManualClock(start.timestamp())
    bus = SimulatedBus(list(addresses), pyrgeometers=list(pyrgeometers), shaded=list(shaded),
                       tracker_active=tracker_active, faults=faults, seed=seed, failure_rate=0,
                       clock=clock, realtime=False, cloud_mean_gap_s=cloud_mean_gap_s)
    sensors = [SMPSensor(bus, a, reg.PYRGEOMETER if a in pyrgeometers else reg.PYRANOMETER) for a in addresses]
    for s in sensors:
        s.clear_error()
    own = writer is None
    if own:
        writer = Writer(db)
        require(writer.open())
    require(writer.start(run_id, {"simulated": True, "config": {
        "addresses": list(addresses), "pyrgeometers": list(pyrgeometers), "interval_s": step_s, "label": run_id}}))
    agg = MinuteAggregator(step_s)
    tracker = EventTracker()
    cycles = int(hours * 3600 / step_s)
    for cycle in range(cycles):
        moment = datetime.fromtimestamp(clock.t, timezone.utc)
        samples = []
        for sensor in sensors:
            try:
                sample = sensor.read_sample()
            except PortError:
                continue
            sample.time_utc = moment
            samples.append(sample)
        minutes = [m for s in samples for m in agg.add(s)]
        events = [e for s in samples for e in tracker.observe(s)] + tracker.tick(int(clock.t * 1000))
        require(writer.commit_cycle(run_id, cycle, samples, {"cycle": cycle}, minutes=minutes, events=events))
        clock.advance(step_s)
    require(writer.finish(run_id, "duration", {"simulated": True, "config": {
        "addresses": list(addresses), "pyrgeometers": list(pyrgeometers), "interval_s": step_s}},
        minutes=agg.flush()))
    if own:
        writer.close()
    return {"start_ms": int(start.timestamp() * 1000), "end_ms": int(clock.t * 1000), "cycles": cycles}


def version(config: dict, valid_from_ms: int = 0, version_id: int = 1) -> dict:
    return {"id": version_id, "valid_from_ms": valid_from_ms, "config": config}


def config(*, site=True, solys2=False, shaded=(), addresses=(2, 3, 4), qc=None) -> dict:
    return {"site": SITE if site else None, "solys2_in_use": solys2,
            "instruments": [{"address": a, "shaded": a in shaded, "label": ""} for a in addresses],
            **({"qc": qc} if qc else {})}


def annotated(db: Path, versions: list[dict], start_ms: int, end_ms: int, minutes: bool = False,
              addresses=(2, 3, 4)) -> list[dict]:
    reader = Reader(db)
    timeline = ConfigTimeline.build(versions, addresses)
    if minutes:
        rows = require(reader.range_minutes(start_ms, end_ms))["items"]
        return annotate_minutes(rows, timeline)
    rows, after = [], None
    while True:
        page = require(reader.range_samples(start_ms, end_ms, after=after, limit=20_000))
        rows.extend(page["items"])
        after = page["next"]
        if after is None:
            break
    return annotate_samples(rows, timeline)

"""Bucle de adquisición: sondea los sensores en serie y guarda CSV.

Por cada prueba se crea una carpeta data/<fecha>_<hora>[_etiqueta]/ con:
  raw_1s.csv      una fila por sensor y ciclo (formato largo)
  minute_avg.csv  promedios de 1 minuto por sensor (recomendación §9.2 del manual)
  run.json        configuración, identificación de sensores y estadísticas del bus

La magnitud que se promedia depende del tipo de sensor: irradiancia global en
los piranómetros y onda larga descendente Ld↓ en el pirgeómetro.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import TextIO

from . import console
from . import registers as reg
from .clock import ClockMonitor, clock_event, clock_status
from .events import ERROR, INFO, Event, EventTracker, now_ms
from .minutes import MINUTE_COLUMNS, MinuteAggregator, MinuteRecord, fmt as _fmt
from .sensor import DeviceInfo, Sample, SMPSensor
from .transport import BusError, ModbusBus, PortError, read_transaction_wire_time_s
from .result import require
from .storage import Writer
from .tracing import emit, traced

RAW_COLUMNS = [
    "time_utc", "time_local", "cycle", "address", "sensor_type",
    "irradiance_wm2", "raw_irradiance_wm2", "lw_down_wm2", "raw_lw_down_wm2", "stdev_wm2",
    "body_temp_c", "supply_v", "rh_pct", "pressure_hpa",
    "mode", "status_hex", "status_flags", "valid", "rtt_ms", "error",
]
__all__ = ["Acquisition", "AcquisitionConfig", "RAW_COLUMNS", "MINUTE_COLUMNS", "raw_row"]

BACKOFF_AFTER_FAILURES = 5   # fallos seguidos antes de espaciar las consultas
BACKOFF_EVERY_CYCLES = 10    # un sensor mudo solo se reintenta cada N ciclos
CLEAR_ERROR_MIN_GAP_S = 30.0
PORT_RETRY_S = 2.0


@dataclass
class AcquisitionConfig:
    addresses: list[int]
    pyrgeometers: list[int] = field(default_factory=list)  # subconjunto de addresses
    interval_s: float = 1.0
    duration_s: float | None = None
    diag_every_s: float = 60.0
    clear_errors: bool = True
    out_dir: Path = Path("data")
    label: str = ""
    simulated: bool = False
    live: bool = True
    db_path: Path | None = None
    ntp_server: str = ""              # vacío: sin comprobación de reloj
    clock_check_s: float = 600.0
    clock_max_offset_s: float = 1.0
    archive_dir: Path | None = None   # None: sin archivo Parquet ni poda
    retention_days: int = 30          # 0: nunca podar la base activa
    archive_every_s: float = 3600.0
    summary_every_s: float = 0        # > 0: una línea de promedios cada N s en vez de una por ciclo


def raw_row(cycle: int, s: Sample) -> list:
    return [
        s.time_utc.isoformat(timespec="milliseconds"),
        s.time_utc.astimezone().isoformat(timespec="milliseconds"),
        cycle,
        s.address,
        s.sensor_type,
        _fmt(s.irradiance, 2),
        _fmt(s.raw_irradiance, 2),
        _fmt(s.lw_down, 2),
        _fmt(s.raw_lw_down, 2),
        _fmt(s.stdev),
        _fmt(s.body_temp_c),
        _fmt(s.supply_v),
        _fmt(s.rh_pct),
        _fmt(s.pressure_hpa),
        "" if s.mode is None else s.mode,
        "" if s.status is None else f"0x{s.status:04X}",
        "|".join(s.status_names),
        int(s.valid),
        _fmt(s.rtt_ms),
        s.error,
    ]


@dataclass
class _SensorState:
    requests: int = 0
    ok: int = 0
    valid: int = 0
    consecutive_failures: int = 0
    last_clear: float = -math.inf
    errors_cleared: int = 0
    rtt_ms: list[float] = field(default_factory=list)
    last_error: str = ""
    flags_seen: set[str] = field(default_factory=set)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


class Acquisition:
    def __init__(self, bus: ModbusBus, cfg: AcquisitionConfig) -> None:
        self.bus = bus
        self.cfg = cfg
        self.sensors = [
            SMPSensor(bus, a, reg.PYRGEOMETER if a in cfg.pyrgeometers else reg.PYRANOMETER)
            for a in cfg.addresses
        ]
        self.state = {a: _SensorState() for a in cfg.addresses}
        self.infos: dict[int, DeviceInfo] = {}
        self.cycle_ms: list[float] = []
        self.overruns = 0
        self.port_errors = 0
        self.port_downtime_s = 0.0
        if not math.isfinite(cfg.interval_s) or cfg.interval_s <= 0:
            raise ValueError("interval_s debe ser positivo y finito")
        if len(set(cfg.addresses)) != len(cfg.addresses) or not cfg.addresses:
            raise ValueError("Las direcciones deben ser únicas y no vacías")
        self.run_id = uuid4().hex
        self.writer: Writer | None = None
        self._deadline: float | None = None
        self.tracker = EventTracker()
        self.pending_events: list[Event] = []
        self.clock_monitor: ClockMonitor | None = None
        self.clock: dict | None = None
        self.archiver = None
        self.prune_queue: deque[tuple[str, int, int]] = deque()
        self.archive_state: dict = {"archived_days": 0, "pruned_days": 0, "last_error": ""}
        self.summary = console.Summary(cfg.summary_every_s or 10)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        name = f"{stamp}_{cfg.label}" if cfg.label else stamp
        if cfg.simulated:
            name += "_SIM"
        self.run_dir = cfg.out_dir / name
        self.raw_path = self.run_dir / "raw_1s.csv"
        self.minute_path = self.run_dir / "minute_avg.csv"
        self.meta_path = self.run_dir / "run.json"

    # ---------------------------------------------------------------- helpers
    def _log(self, msg: str) -> None:
        print(msg, flush=True)

    def _identify(self) -> None:
        for sensor in self.sensors:
            try:
                info = sensor.read_info()
            except BusError as exc:
                self._log(f"  #{sensor.address}: SIN RESPUESTA ({exc})")
                continue
            self.infos[sensor.address] = info
            self._log(
                f"  #{sensor.address}: {info.model} ({reg.SENSOR_LABELS[sensor.sensor_type]})  "
                f"serie {info.serial_label}  "
                f"data model {info.datamodel_version}  sw {info.software_version}  "
                f"hw {info.hardware_version}"
            )
            for warning in info.warnings:
                self._log(f"      aviso: {warning}")

    def _clear_error(self, sensor: SMPSensor, st: _SensorState, now: float) -> None:
        if now - st.last_clear < CLEAR_ERROR_MIN_GAP_S:
            return
        st.last_clear = now
        try:
            sensor.clear_error()
            st.errors_cleared += 1
            self.pending_events.append(Event(now_ms(), "error_cleared", INFO, sensor.address,
                                             {"coil": reg.IO_CLEAR_ERROR}))
        except BusError as exc:
            st.last_error = f"clear_error: {exc}"

    def _recover_port(self, exc: PortError) -> None:
        self.port_errors += 1
        lost_at = time.monotonic()
        self.pending_events.append(Event(now_ms(), "port_lost", ERROR, None, {"error": str(exc)}))
        self._log(f"!! Puerto perdido: {exc}. Reintentando cada {PORT_RETRY_S:.0f} s (Ctrl+C para salir)")
        recovered = False
        try:
            while True:
                if self._deadline is not None and time.monotonic() >= self._deadline:
                    raise TimeoutError("Duración alcanzada durante reconexión")
                time.sleep(PORT_RETRY_S)
                try:
                    self.bus.reopen()
                    recovered = True
                    self._log("!! Puerto recuperado")
                    return
                except PortError:
                    continue
        finally:
            downtime = time.monotonic() - lost_at
            self.port_downtime_s += downtime
            if recovered:
                self.pending_events.append(Event(now_ms(), "port_recovered", INFO, None,
                                                 {"downtime_s": round(downtime, 1)}))

    # ------------------------------------------------- reloj, archivo, eventos
    def _start_background(self) -> None:
        cfg = self.cfg
        if cfg.ntp_server:
            self.clock_monitor = ClockMonitor(cfg.ntp_server, cfg.clock_check_s)
            self.clock_monitor.start()
        if cfg.archive_dir is not None and cfg.db_path is not None:
            from .archive import ArchiveWorker
            self.archiver = ArchiveWorker(cfg.db_path, cfg.archive_dir, cfg.retention_days, cfg.archive_every_s)
            self.archiver.start()

    def _stop_background(self) -> None:
        for worker in (self.clock_monitor, self.archiver):
            if worker is not None:
                worker.stop()

    def _collect_background(self) -> None:
        """Convierte resultados de los hilos en eventos y registros; nunca bloquea el ciclo."""
        if self.clock_monitor is not None:
            for check in self.clock_monitor.drain():
                self.clock = clock_status(check, self.cfg.clock_max_offset_s)
                self.pending_events.append(clock_event(check, self.cfg.clock_max_offset_s))
        if self.archiver is None:
            return
        for kind, day, payload in self.archiver.drain():
            if kind == "archived":
                require(self.writer.record_archive(day, payload.path, payload.rows, payload.sha256))
                self.archiver.done(day)
                self.archive_state["archived_days"] += 1
                self.pending_events.append(Event(now_ms(), "archive_written", INFO, None,
                                                 {"day": day, "rows": payload.rows, "path": payload.path}))
            elif kind == "prune_ready":
                self.prune_queue.append((day, *payload))
            else:
                self.archive_state["last_error"] = payload
                self.pending_events.append(Event(now_ms(), "archive_failed", ERROR, None,
                                                 {"day": day, "error": payload}))

    def _prune_step(self) -> None:
        """Poda un lote de un día ya archivado y verificado; el resto en ciclos siguientes."""
        from .archive import PRUNE_BATCH
        if not self.prune_queue:
            return
        day, start, end = self.prune_queue[0]
        deleted = require(self.writer.prune_batch(start, end, PRUNE_BATCH))
        if deleted < PRUNE_BATCH and self.writer.count_range(start, end) == 0:
            require(self.writer.mark_pruned(day, [Event(now_ms(), "archive_pruned", INFO, None, {"day": day})]))
            self.prune_queue.popleft()
            self.archive_state["pruned_days"] += 1
            if self.archiver is not None:
                self.archiver.done(day)

    def _take_events(self, t_ms: int) -> list[Event]:
        events = self.pending_events + self.tracker.tick(t_ms)
        self.pending_events = []
        return events

    def _aligned_tick(self) -> float:
        """Próximo instante (en reloj monotónico) que cae en un múltiplo exacto del
        intervalo en el reloj de pared, para que cada minuto tenga 60/intervalo ciclos."""
        wall = time.time()
        interval = self.cfg.interval_s
        return time.monotonic() + (math.ceil(wall / interval) * interval - wall)

    def _write_meta(self, started: datetime, ended: datetime | None, reason: str) -> None:
        cfg = asdict(self.cfg)
        cfg["out_dir"] = str(self.cfg.out_dir)
        cfg["db_path"] = str(self.cfg.db_path) if self.cfg.db_path else None
        per_sensor = {}
        baud, parity = self.bus.baudrate, self.bus.parity
        wire_ms = {
            s.address: read_transaction_wire_time_s(s.block_count, baud, parity) * 1000
            for s in self.sensors
        }
        for sensor in self.sensors:
            address, st = sensor.address, self.state[sensor.address]
            per_sensor[str(address)] = {
                "sensor_type": sensor.sensor_type,
                "theoretical_wire_ms_per_read": round(wire_ms[address], 1),
                "requests": st.requests,
                "responses_ok": st.ok,
                "samples_valid": st.valid,
                "comm_errors": st.requests - st.ok,
                "errors_cleared": st.errors_cleared,
                "flags_seen": sorted(st.flags_seen),
                "rtt_ms_mean": round(statistics.fmean(st.rtt_ms), 1) if st.rtt_ms else None,
                "rtt_ms_p95": _pct(st.rtt_ms, 0.95),
                "rtt_ms_max": max(st.rtt_ms) if st.rtt_ms else None,
                "last_error": st.last_error,
            }
        meta = {
            "run_id": self.run_id,
            "bus": self.bus.description,
            "simulated": self.cfg.simulated,
            "started_local": started.astimezone().isoformat(timespec="seconds"),
            "ended_local": ended.astimezone().isoformat(timespec="seconds") if ended else None,
            "stop_reason": reason,
            "config": cfg,
            "sensors": {str(a): asdict(i) for a, i in self.infos.items()},
            "per_sensor": per_sensor,
            "bus_timing": {
                # Con los baudios y la paridad reales del bus, no los de fábrica
                "baudrate": baud,
                "parity": parity,
                "theoretical_wire_ms_per_cycle": round(sum(wire_ms.values()), 1),
                "cycles": len(self.cycle_ms),
                "cycle_ms_mean": round(statistics.fmean(self.cycle_ms), 1) if self.cycle_ms else None,
                "cycle_ms_p95": _pct(self.cycle_ms, 0.95),
                "cycle_ms_max": max(self.cycle_ms) if self.cycle_ms else None,
                "bus_utilization_pct": (
                    round(100 * statistics.fmean(self.cycle_ms) / (1000 * self.cfg.interval_s), 1)
                    if self.cycle_ms else None
                ),
                "overruns": self.overruns,
                "port_errors": self.port_errors,
                "port_downtime_s": round(self.port_downtime_s, 1),
            },
            "files": {"raw": self.raw_path.name, "minute": self.minute_path.name},
        }
        self.meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def _row(self, cycle: int, s: Sample) -> list:
        return raw_row(cycle, s)

    def _live_line(self, samples: list[Sample], cycle_ms: float) -> str:
        return console.cycle_line(samples, cycle_ms)

    # ------------------------------------------------------------------- run
    @traced("Acquisition.run")
    def run(self) -> Path:
        cfg = self.cfg
        self.run_dir.mkdir(parents=True, exist_ok=False)
        started = datetime.now(timezone.utc)
        self._deadline = time.monotonic() + cfg.duration_s if cfg.duration_s is not None else None
        self._log(f"Bus: {self.bus.description}")
        self._log(
            f"Sensores {cfg.addresses} (pirgeómetros: {cfg.pyrgeometers or 'ninguno'}), "
            f"intervalo {cfg.interval_s:g} s, salida {self.run_dir}"
        )
        self.writer = Writer(cfg.db_path or self.run_dir / "run.sqlite3")
        require(self.writer.open())
        reason = "error"
        initialized = False
        last_minutes: list[MinuteRecord] = []
        try:
            self._write_meta(started, None, "running")
            require(self.writer.start(self.run_id, json.loads(self.meta_path.read_text())))
            initialized = True
            self._log("Identificación:")
            self._identify()
            if cfg.clear_errors:
                for sensor in self.sensors:
                    if sensor.address in self.infos:
                        self._clear_error(sensor, self.state[sensor.address], time.monotonic())
            self._write_meta(started, None, "running")
            # Refresh identity metadata before the first cycle.
            require(self.writer.metadata(self.run_id, json.loads(self.meta_path.read_text())))
            self._start_background()
            with open(self.raw_path, "w", newline="", encoding="utf-8") as raw_f, \
                    open(self.minute_path, "w", newline="", encoding="utf-8") as min_f:
                raw_w, min_w = csv.writer(raw_f), csv.writer(min_f)
                raw_w.writerow(RAW_COLUMNS)
                min_w.writerow(MINUTE_COLUMNS)
                aggregator = MinuteAggregator(cfg.interval_s)
                try:
                    self._loop(raw_f, raw_w, min_f, min_w, aggregator)
                    reason = "duration"
                except TimeoutError:
                    reason = "duration"
                except KeyboardInterrupt:
                    reason = "ctrl+c"
                    self._log("\nDetenido por el usuario.")
                finally:
                    last_minutes = aggregator.flush()
                    for minute in last_minutes:
                        min_w.writerow(minute.csv_row())
        finally:
            self._stop_background()
            try:
                self._write_meta(started, datetime.now(timezone.utc), reason)
                if initialized:
                    pending = self._take_events(now_ms())
                    require(self.writer.finish(self.run_id, reason, json.loads(self.meta_path.read_text()),
                                               minutes=last_minutes, events=pending))
            finally:
                self.writer.close()
        self._print_summary()
        return self.run_dir

    def _loop(self, raw_f: TextIO, raw_w, min_f: TextIO, min_w, aggregator: MinuteAggregator) -> None:
        cfg = self.cfg
        next_tick = self._aligned_tick()
        t_start = next_tick
        last_diag = -math.inf
        cycle = 0
        while True:
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if cfg.duration_s is not None and time.monotonic() - t_start >= cfg.duration_s:
                return
            cycle_start = time.monotonic()
            do_diag = cycle_start - last_diag >= cfg.diag_every_s
            samples: list[Sample] = []
            port_lost = False
            for sensor in self.sensors:
                st = self.state[sensor.address]
                if st.consecutive_failures >= BACKOFF_AFTER_FAILURES and cycle % BACKOFF_EVERY_CYCLES:
                    continue
                try:
                    sample = sensor.read_sample(with_diagnostics=do_diag)
                except PortError as exc:
                    # Perder el USB no es un fallo del sensor: no cuenta como
                    # petición (comm_errors = requests - ok) y va a port_errors.
                    self._recover_port(exc)
                    port_lost = True
                    break
                st.requests += 1
                self._account(sensor, st, sample)
                self.pending_events.extend(self.tracker.observe(sample))
                samples.append(sample)
            cycle_ms = (time.monotonic() - cycle_start) * 1000
            closed: list[MinuteRecord] = []
            for sample in samples:
                closed.extend(aggregator.add(sample))
            self._collect_background()
            require(self.writer.commit_cycle(self.run_id, cycle, samples, {
                "cycle": cycle, "cycle_ms": cycle_ms, "port_errors": self.port_errors,
                "overruns": self.overruns, "port_downtime_s": self.port_downtime_s,
                "per_sensor": {str(a): {"requests": s.requests, "responses_ok": s.ok,
                    "samples_valid": s.valid, "consecutive_failures": s.consecutive_failures,
                    "comm_errors": s.requests - s.ok} for a, s in self.state.items()},
                "clock": self.clock, "clock_check_enabled": bool(self.cfg.ntp_server),
                "archive": {**self.archive_state, "enabled": self.archiver is not None,
                            "retention_days": self.cfg.retention_days, "prune_queue": len(self.prune_queue)},
            }, minutes=closed, events=(events := self._take_events(now_ms()))))
            for sample in samples:
                raw_w.writerow(raw_row(cycle, sample))
            for minute in closed:
                min_w.writerow(minute.csv_row())
            raw_f.flush()
            min_f.flush()
            self._prune_step()
            if cfg.live and samples:
                if cfg.summary_every_s > 0:
                    line = self.summary.add(samples, cycle_ms, time.monotonic())
                    if line:
                        self._log(line)
                else:
                    self._log(self._live_line(samples, cycle_ms))
            for event in events:  # ya confirmados en la base
                if cfg.live or event.severity != "info":
                    self._log(console.event_line(event))
            cycle += 1
            if port_lost:
                # El ciclo cortado incluye la reconexión (≥ 2 s): no entra en las
                # estadísticas de ciclo, y los ciclos que no se hicieron mientras
                # no había puerto tampoco cuentan como overruns.
                next_tick = self._aligned_tick()
                continue
            if do_diag:
                last_diag = cycle_start
            self.cycle_ms.append(cycle_ms)
            next_tick += cfg.interval_s
            now = time.monotonic()
            if now > next_tick:
                missed = int((now - next_tick) // cfg.interval_s) + 1
                self.overruns += missed
                next_tick += missed * cfg.interval_s

    def _account(self, sensor: SMPSensor, st: _SensorState, sample: Sample) -> None:
        if not sample.ok:
            # El aviso sale como evento sensor_silent / sensor_recovered (una línea legible).
            st.consecutive_failures += 1
            st.last_error = sample.error
            return
        st.consecutive_failures = 0
        st.ok += 1
        st.valid += int(sample.valid)
        if sample.rtt_ms is not None:
            st.rtt_ms.append(sample.rtt_ms)
        st.flags_seen.update(sample.status_names)
        if self.cfg.clear_errors and sample.status and sample.status & reg.CLEARABLE_MASK:
            self._clear_error(sensor, st, time.monotonic())

    def _print_summary(self) -> None:
        self._log("\nResumen de comunicación:")
        for address, st in self.state.items():
            rtt = f"{statistics.fmean(st.rtt_ms):.0f} ms" if st.rtt_ms else "-"
            flags = ", ".join(sorted(st.flags_seen)) or "ninguno"
            self._log(
                f"  #{address}: {st.ok}/{st.requests} respuestas, {st.valid} válidas, "
                f"RTT medio {rtt}, flags vistos: {flags}"
            )
        if self.cycle_ms:
            mean = statistics.fmean(self.cycle_ms)
            self._log(
                f"  ciclo medio {mean:.0f} ms (p95 {_pct(self.cycle_ms, 0.95):.0f} ms) -> "
                f"ocupación del bus ~{100 * mean / (1000 * self.cfg.interval_s):.0f} % "
                f"a {self.cfg.interval_s:g} s; ciclos perdidos: {self.overruns}"
            )
        self._log(f"Datos en: {self.run_dir}")

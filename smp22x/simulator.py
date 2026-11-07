"""Bus simulado para probar el software sin el PMU485 conectado.

Imita lo necesario del comportamiento real: tiempos de trama según baudios y
paridad, sensores que solo contestan a 19200 8E1 (fábrica, §8.1), IO_ERROR_FLAG
activo hasta que se escribe IO_CLEAR_ERROR y algún timeout ocasional.

El cielo sigue al sol del sitio simulado: noche, amanecer y mediodía con un
modelo de cielo despejado simple, más nubes que bloquean el haz directo. Un
piranómetro sin sombra ve la global; uno bajo la bola de sombra del SOLYS2 ve la
difusa mientras el seguidor funciona. Se pueden inyectar fallas por dirección
(domo sucio, desnivel, bola desalineada, offset, flags, silencio) y controlar el
reloj para recorrer un día entero en segundos. Los datos generados NO son
mediciones.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from . import registers as reg
from .sensor import blackbody_wm2
from .solar import solar_position
from .transport import BusError, read_transaction_wire_time_s

TURNAROUND_S = 0.006  # tiempo de procesado supuesto del esclavo
MAX_CLOUD_DEPTH = 0.7  # atenuación de la nube más densa del simulador
# Sitio por defecto: el del vector de referencia de NREL SPA (Golden, Colorado).
DEFAULT_SITE = (39.742476, -105.1786, 1830.14)


@dataclass
class Faults:
    """Fallas inyectables en un instrumento simulado."""
    gain_drift_per_day: float = 0.0      # fracción por día (−0,01 = domo que pierde 1 %/día)
    tilt_deg: float = 0.0                # desnivel del sensor
    tilt_azimuth_deg: float = 0.0        # hacia dónde cae el desnivel (0 = norte)
    shade_miss: float = 0.0              # fracción del haz directo que la bola no tapa (1 = no tapa)
    offset_wm2: float = 0.0              # offset constante (p. ej. offset térmico nocturno)
    status_bits: int = 0                 # flags fijos (p. ej. 1 << 11 desecante)
    void_every_s: float | None = None    # VOID_DATA un segundo cada N segundos
    silent: tuple[float, float] | None = None  # sin respuesta entre (inicio, fin) en s desde el arranque


class _Sky:
    """Componentes de la radiación en el sitio simulado, en función del tiempo absoluto."""

    def __init__(self, rng: random.Random, site: tuple[float, float, float], cloud_mean_gap_s: float | None) -> None:
        self.rng = rng
        self.site = site
        self.cloud_mean_gap_s = cloud_mean_gap_s
        self.clouds: list[tuple[float, float, float]] = []  # (inicio, duración, atenuación)
        self._horizon: float | None = None
        self._cache: tuple[float, dict] | None = None

    def _extend(self, t: float) -> None:
        if self.cloud_mean_gap_s is None:
            return
        if self._horizon is None:
            self._horizon = t - 60
        while self._horizon < t + 120:
            start = self._horizon + self.rng.expovariate(1 / self.cloud_mean_gap_s)
            duration = self.rng.uniform(8, 35)
            depth = self.rng.uniform(0.25, MAX_CLOUD_DEPTH)
            self.clouds.append((start, duration, depth))
            self._horizon = start + duration
        self.clouds = [c for c in self.clouds if c[0] + c[1] + 10 > t - 120]

    def attenuation(self, t: float) -> float:
        """Fracción del haz directo que bloquea la nube actual (0 = despejado)."""
        self._extend(t)
        attenuation = 0.0
        for start, duration, depth in self.clouds:
            if start - 5 < t < start + duration + 5:
                edge_in = 0.5 * (1 + math.tanh((t - start) / 1.5))
                edge_out = 0.5 * (1 - math.tanh((t - start - duration) / 1.5))
                attenuation = max(attenuation, depth * edge_in * edge_out)
        return attenuation

    def state(self, t: float) -> dict:
        if self._cache is not None and self._cache[0] == t:
            return self._cache[1]
        lat, lon, alt = self.site
        pos = solar_position(t * 1000, lat, lon, alt)
        mu0, sa = float(pos.mu0[0]), float(pos.extraterrestrial[0])
        zenith, azimuth = float(pos.zenith[0]), float(pos.azimuth[0])
        a = self.attenuation(t)
        if mu0 <= 0:
            dni = dhi = 0.0
        else:
            air_mass = 1 / (mu0 + 0.50572 * (96.07995 - min(zenith, 96)) ** -1.6364)
            dni_clear = sa * 0.7 ** (air_mass ** 0.678)
            dhi_clear = 0.08 * sa * mu0
            dni = dni_clear * (1 - a / MAX_CLOUD_DEPTH)
            dhi = dhi_clear + 0.35 * (a / MAX_CLOUD_DEPTH) * dni_clear * mu0
        state = {"zenith": zenith, "azimuth": azimuth, "mu0": mu0, "dni": dni, "dhi": dhi,
                 "ghi": dni * mu0 + dhi, "lwd": 320 + 90 * a / MAX_CLOUD_DEPTH}
        self._cache = (t, state)
        return state


class _FakeSensor:
    """Parte común: identificación, flags de arranque y registros de diagnóstico."""

    device_type = 636
    datamodel = 108
    software_version = 110
    hardware_version = 3

    def __init__(self, address: int, bus: "SimulatedBus") -> None:
        self.address = address
        self.bus = bus
        self.sky = bus.sky
        self.rng = bus.rng
        self.faults = bus.faults.get(address, Faults())
        self.status = (1 << 3) | (1 << 8)  # ERROR + POWER_FAILED tras el arranque
        self._since_last_read: list[float] = []

    def _stdev_since_last_read(self, value: float) -> float:
        self._since_last_read.append(value)
        values = self._since_last_read
        mean = sum(values) / len(values)
        stdev = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        self._since_last_read = [value]
        return stdev

    def body_temp_c(self, t: float) -> float:
        return 24 + 2 * math.sin(t / 600) + 0.3 * self.address

    def flags(self, t: float) -> int:
        status = self.status | self.faults.status_bits
        every = self.faults.void_every_s
        if every and int(t - self.bus.t0) % int(every) == 0:
            status |= 1 << 0
        return status

    def _common(self, t: float) -> dict[int, int]:
        return {
            reg.IO_DEVICE_TYPE: self.device_type,
            reg.IO_DATAMODEL_VERSION: self.datamodel,
            reg.IO_OPERATIONAL_MODE: reg.MODE_NORMAL,
            reg.IO_STATUS_FLAGS: self.flags(t),
            reg.IO_EXT_POWER_SENSOR: 120,
            reg.IO_RH: 52,
            reg.IO_PRESSURE: 10131,
            reg.IO_BATCH_NUMBER: 25,
            reg.IO_SERIAL_NUMBER: 1000 + self.address,
            reg.IO_SOFTWARE_VERSION: self.software_version,
            reg.IO_HARDWARE_VERSION: self.hardware_version,
            reg.IO_NODE_ID: self.address,
        }

    def registers(self, t: float) -> dict[int, int]:
        raise NotImplementedError


class _FakeSMP(_FakeSensor):
    def __init__(self, address: int, bus: "SimulatedBus") -> None:
        super().__init__(address, bus)
        self.gain = 1 + self.rng.uniform(-0.008, 0.008)
        self.offset = self.rng.uniform(-1.0, 1.0)

    def _beam_on_sensor(self, sky: dict) -> float:
        """Haz directo sobre el plano del sensor, con desnivel si lo hay."""
        if sky["mu0"] <= 0:
            return 0.0
        tilt = math.radians(self.faults.tilt_deg)
        z, az = math.radians(sky["zenith"]), math.radians(sky["azimuth"])
        cos_inc = math.cos(z) * math.cos(tilt) + math.sin(z) * math.sin(tilt) * math.cos(
            az - math.radians(self.faults.tilt_azimuth_deg))
        return sky["dni"] * max(cos_inc, 0.0)

    def irradiance(self, t: float) -> float:
        sky = self.sky.state(t)
        beam = self._beam_on_sensor(sky)
        if self.address in self.bus.shaded and self.bus.tracker_active:
            beam *= self.faults.shade_miss
        days = (t - self.bus.t0) / 86400
        gain = self.gain * (1 + self.faults.gain_drift_per_day * days)
        noise = self.rng.gauss(0, 0.6)
        return (beam + sky["dhi"]) * gain + self.offset + self.faults.offset_wm2 + noise

    def registers(self, t: float) -> dict[int, int]:
        irr = self.irradiance(t)
        stdev = self._stdev_since_last_read(irr)
        values = self._common(t)
        values.update({
            reg.IO_SCALE_FACTOR: 1,
            reg.IO_SENSOR1_DATA: round(irr * 10) & 0xFFFF,
            reg.IO_RAW_SENSOR1_DATA: round(irr * 1.004 * 10) & 0xFFFF,
            reg.IO_STDEV_SENSOR1: round(stdev * 10),
            reg.IO_BODY_TEMPERATURE: round(self.body_temp_c(t) * 10),
        })
        return values


class _FakeSGR4(_FakeSensor):
    """Pirgeómetro: IO_SENSOR1_DATA es la radiación neta (negativa con cielo
    despejado) e IO_SENSOR2_DATA es Ld↓ = neta + σT⁴ (manual SGR4 §7.1.5)."""

    # Valores leídos al SGR4 real en la prueba 01 (docs/prueba-01.md)
    device_type = 611
    datamodel = 102
    software_version = 300
    hardware_version = 5

    def registers(self, t: float) -> dict[int, int]:
        body_t = self.body_temp_c(t)
        ld = self.sky.state(t)["lwd"] + self.faults.offset_wm2 + self.rng.gauss(0, 0.8)
        net = ld - blackbody_wm2(body_t)
        stdev = self._stdev_since_last_read(net)
        values = self._common(t)
        values.update({
            reg.IO_SCALE_FACTOR: 1,
            reg.IO_SENSOR1_DATA: round(net * 10) & 0xFFFF,
            reg.IO_RAW_SENSOR1_DATA: round(net * 1.003 * 10) & 0xFFFF,
            reg.IO_STDEV_SENSOR1: round(stdev * 10),
            reg.IO_BODY_TEMPERATURE: round(body_t * 10),
            reg.IO_SENSOR2_DATA: round(ld * 10) & 0xFFFF,
            reg.IO_RAW_SENSOR2_DATA: round((net * 1.003 + blackbody_wm2(body_t)) * 10) & 0xFFFF,
        })
        return values


class SimulatedBus:
    def __init__(
        self,
        addresses: list[int],
        baudrate: int = reg.DEFAULT_BAUDRATE,
        parity: str = reg.DEFAULT_PARITY,
        timeout: float = 0.3,
        failure_rate: float = 0.003,
        seed: int | None = None,
        pyrgeometers: list[int] | None = None,
        site: tuple[float, float, float] | None = None,
        shaded: list[int] | None = None,
        tracker_active: bool = True,
        faults: dict[int, Faults] | None = None,
        clock: Callable[[], float] | None = None,
        realtime: bool = True,
        cloud_mean_gap_s: float | None = 40.0,
    ) -> None:
        self.description = f"SIMULADO ({baudrate} baud 8{parity}1)"
        self.baudrate = baudrate
        self.parity = parity
        self.timeout = timeout
        self.failure_rate = failure_rate
        self.rng = random.Random(seed)
        self.clock = clock or time.time
        self.realtime = realtime
        self.t0 = self.clock()
        self.shaded = set(shaded or [])
        self.tracker_active = tracker_active
        self.faults = faults or {}
        self.sky = _Sky(self.rng, site or DEFAULT_SITE, cloud_mean_gap_s)
        pyrgeometers = pyrgeometers or []
        self.sensors: dict[int, _FakeSensor] = {
            a: (_FakeSGR4 if a in pyrgeometers else _FakeSMP)(a, self) for a in addresses
        }
        # Los sensores simulados tienen la configuración de fábrica: con otros
        # baudios o paridad no entienden la trama y no contestan.
        self._line_matches = (baudrate, parity) == (reg.DEFAULT_BAUDRATE, reg.DEFAULT_PARITY)

    def _wait(self, seconds: float) -> None:
        if self.realtime:
            time.sleep(seconds)

    def _no_response(self, slave: int) -> BusError:
        self._wait(self.timeout)
        return BusError(f"NoResponseError: sin respuesta del esclavo {slave} (simulado)")

    def _silent(self, sensor: _FakeSensor, t: float) -> bool:
        window = sensor.faults.silent
        return window is not None and window[0] <= t - self.t0 < window[1]

    def read_input_registers(self, slave: int, start: int, count: int) -> list[int]:
        sensor = self.sensors.get(slave)
        t = self.clock()
        if sensor is None or not self._line_matches or self._silent(sensor, t) or self.rng.random() < self.failure_rate:
            raise self._no_response(slave)
        self._wait(read_transaction_wire_time_s(count, self.baudrate, self.parity) + TURNAROUND_S)
        values = sensor.registers(t)
        return [values.get(start + i, 0) for i in range(count)]

    def write_coil(self, slave: int, coil: int, value: bool) -> None:
        sensor = self.sensors.get(slave)
        if sensor is None or not self._line_matches:
            raise self._no_response(slave)
        self._wait(read_transaction_wire_time_s(0, self.baudrate, self.parity) + TURNAROUND_S)
        if coil == reg.IO_CLEAR_ERROR and value:
            sensor.status &= ~reg.CLEARABLE_MASK

    def reopen(self) -> None:
        pass

    def close(self) -> None:
        pass


@dataclass
class ManualClock:
    """Reloj controlable para recorrer escenarios sin esperar."""
    t: float = field(default_factory=time.time)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

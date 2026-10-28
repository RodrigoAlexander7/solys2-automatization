"""Bus simulado para probar el software sin el PMU485 conectado.

Imita lo necesario del comportamiento real: tiempos de trama según baudios y
paridad, sensores que solo contestan a 19200 8E1 (fábrica, §8.1), IO_ERROR_FLAG
activo hasta que se escribe IO_CLEAR_ERROR, algún timeout ocasional y un cielo
con nubes que ven todos los sensores a la vez. Los piranómetros bajan con cada
nube (cada uno con su propia ganancia, offset y ruido) y el pirgeómetro sube,
porque una nube es más cálida que el cielo despejado. Los datos generados NO
son mediciones.
"""

from __future__ import annotations

import math
import random
import time

from . import registers as reg
from .sensor import blackbody_wm2
from .transport import BusError, read_transaction_wire_time_s

TURNAROUND_S = 0.006  # tiempo de procesado supuesto del esclavo
MAX_CLOUD_DEPTH = 0.7  # atenuación de la nube más densa del simulador


class _Sky:
    """Irradiancia "verdadera" común: base ~820 W/m² con pasos de nubes."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.t0 = time.monotonic()
        self.clouds: list[tuple[float, float, float]] = []  # (inicio, duración, atenuación)
        self._horizon = 0.0

    def _extend(self, t: float) -> None:
        while self._horizon < t + 120:
            start = self._horizon + self.rng.expovariate(1 / 40)
            duration = self.rng.uniform(8, 35)
            depth = self.rng.uniform(0.25, MAX_CLOUD_DEPTH)
            self.clouds.append((start, duration, depth))
            self._horizon = start + duration

    def _elapsed(self) -> float:
        t = time.monotonic() - self.t0
        self._extend(t)
        return t

    def attenuation(self) -> float:
        """Fracción de la irradiancia que bloquea la nube actual (0 = despejado)."""
        t = self._elapsed()
        attenuation = 0.0
        for start, duration, depth in self.clouds:
            if start - 5 < t < start + duration + 5:
                edge_in = 0.5 * (1 + math.tanh((t - start) / 1.5))
                edge_out = 0.5 * (1 - math.tanh((t - start - duration) / 1.5))
                attenuation = max(attenuation, depth * edge_in * edge_out)
        return attenuation

    def irradiance(self) -> float:
        t = self._elapsed()
        base = 820 + 25 * math.sin(2 * math.pi * t / 900)
        return base * (1 - self.attenuation())

    def lw_down(self) -> float:
        """Onda larga descendente: ~320 W/m² con cielo despejado, hasta ~410 bajo nube densa."""
        return 320 + 90 * self.attenuation() / MAX_CLOUD_DEPTH


class _FakeSensor:
    """Parte común: identificación, flags de arranque y registros de diagnóstico."""

    device_type = 636
    datamodel = 108
    software_version = 110
    hardware_version = 3

    def __init__(self, address: int, sky: _Sky, rng: random.Random) -> None:
        self.address = address
        self.sky = sky
        self.rng = rng
        self.status = (1 << 3) | (1 << 8)  # ERROR + POWER_FAILED tras el arranque
        self._since_last_read: list[float] = []

    def _stdev_since_last_read(self, value: float) -> float:
        self._since_last_read.append(value)
        values = self._since_last_read
        mean = sum(values) / len(values)
        stdev = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        self._since_last_read = [value]
        return stdev

    def body_temp_c(self) -> float:
        return 24 + 2 * math.sin(time.monotonic() / 600) + 0.3 * self.address

    def _common(self) -> dict[int, int]:
        return {
            reg.IO_DEVICE_TYPE: self.device_type,
            reg.IO_DATAMODEL_VERSION: self.datamodel,
            reg.IO_OPERATIONAL_MODE: reg.MODE_NORMAL,
            reg.IO_STATUS_FLAGS: self.status,
            reg.IO_EXT_POWER_SENSOR: 120,
            reg.IO_RH: 52,
            reg.IO_PRESSURE: 10131,
            reg.IO_BATCH_NUMBER: 25,
            reg.IO_SERIAL_NUMBER: 1000 + self.address,
            reg.IO_SOFTWARE_VERSION: self.software_version,
            reg.IO_HARDWARE_VERSION: self.hardware_version,
            reg.IO_NODE_ID: self.address,
        }

    def registers(self) -> dict[int, int]:
        raise NotImplementedError


class _FakeSMP(_FakeSensor):
    def __init__(self, address: int, sky: _Sky, rng: random.Random) -> None:
        super().__init__(address, sky, rng)
        self.gain = 1 + rng.uniform(-0.008, 0.008)
        self.offset = rng.uniform(-2.0, 2.0)

    def registers(self) -> dict[int, int]:
        irr = self.sky.irradiance() * self.gain + self.offset + self.rng.gauss(0, 0.6)
        stdev = self._stdev_since_last_read(irr)
        values = self._common()
        values.update({
            reg.IO_SCALE_FACTOR: 1,
            reg.IO_SENSOR1_DATA: round(irr * 10) & 0xFFFF,
            reg.IO_RAW_SENSOR1_DATA: round(irr * 1.004 * 10) & 0xFFFF,
            reg.IO_STDEV_SENSOR1: round(stdev * 10),
            reg.IO_BODY_TEMPERATURE: round(self.body_temp_c() * 10),
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

    def registers(self) -> dict[int, int]:
        body_t = self.body_temp_c()
        ld = self.sky.lw_down() + self.rng.gauss(0, 0.8)
        net = ld - blackbody_wm2(body_t)
        stdev = self._stdev_since_last_read(net)
        values = self._common()
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
    ) -> None:
        self.description = f"SIMULADO ({baudrate} baud 8{parity}1)"
        self.baudrate = baudrate
        self.parity = parity
        self.timeout = timeout
        self.failure_rate = failure_rate
        self.rng = random.Random(seed)
        sky = _Sky(self.rng)
        pyrgeometers = pyrgeometers or []
        self.sensors: dict[int, _FakeSensor] = {
            a: (_FakeSGR4 if a in pyrgeometers else _FakeSMP)(a, sky, self.rng) for a in addresses
        }
        # Los sensores simulados tienen la configuración de fábrica: con otros
        # baudios o paridad no entienden la trama y no contestan.
        self._line_matches = (baudrate, parity) == (reg.DEFAULT_BAUDRATE, reg.DEFAULT_PARITY)

    def _no_response(self, slave: int) -> BusError:
        time.sleep(self.timeout)
        return BusError(f"NoResponseError: sin respuesta del esclavo {slave} (simulado)")

    def read_input_registers(self, slave: int, start: int, count: int) -> list[int]:
        sensor = self.sensors.get(slave)
        if sensor is None or not self._line_matches or self.rng.random() < self.failure_rate:
            raise self._no_response(slave)
        time.sleep(read_transaction_wire_time_s(count, self.baudrate, self.parity) + TURNAROUND_S)
        values = sensor.registers()
        return [values.get(start + i, 0) for i in range(count)]

    def write_coil(self, slave: int, coil: int, value: bool) -> None:
        sensor = self.sensors.get(slave)
        if sensor is None or not self._line_matches:
            raise self._no_response(slave)
        time.sleep(read_transaction_wire_time_s(0, self.baudrate, self.parity) + TURNAROUND_S)
        if coil == reg.IO_CLEAR_ERROR and value:
            sensor.status &= ~reg.CLEARABLE_MASK

    def reopen(self) -> None:
        pass

    def close(self) -> None:
        pass

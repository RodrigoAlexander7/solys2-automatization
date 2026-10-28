"""Bus simulado para probar el software sin el PMU485 conectado.

Imita lo necesario del comportamiento real: tiempos de trama a 19200 baudios,
IO_ERROR_FLAG activo hasta que se escribe IO_CLEAR_ERROR, algún timeout
ocasional y un cielo con nubes que ven los tres sensores a la vez (cada uno con
su propia ganancia, offset y ruido). Los datos generados NO son mediciones.
"""

from __future__ import annotations

import math
import random
import time

from . import registers as reg
from .transport import BusError, read_transaction_wire_time_s

TURNAROUND_S = 0.006  # tiempo de procesado supuesto del esclavo


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
            depth = self.rng.uniform(0.25, 0.7)
            self.clouds.append((start, duration, depth))
            self._horizon = start + duration

    def irradiance(self) -> float:
        t = time.monotonic() - self.t0
        self._extend(t)
        base = 820 + 25 * math.sin(2 * math.pi * t / 900)
        attenuation = 0.0
        for start, duration, depth in self.clouds:
            if start - 5 < t < start + duration + 5:
                edge_in = 0.5 * (1 + math.tanh((t - start) / 1.5))
                edge_out = 0.5 * (1 - math.tanh((t - start - duration) / 1.5))
                attenuation = max(attenuation, depth * edge_in * edge_out)
        return base * (1 - attenuation)


class _FakeSMP:
    def __init__(self, address: int, sky: _Sky, rng: random.Random) -> None:
        self.address = address
        self.sky = sky
        self.rng = rng
        self.gain = 1 + rng.uniform(-0.008, 0.008)
        self.offset = rng.uniform(-2.0, 2.0)
        self.status = (1 << 3) | (1 << 8)  # ERROR + POWER_FAILED tras el arranque
        self._since_last_read: list[float] = []

    def registers(self) -> dict[int, int]:
        true_irr = self.sky.irradiance()
        irr = true_irr * self.gain + self.offset + self.rng.gauss(0, 0.6)
        self._since_last_read.append(irr)
        values = self._since_last_read
        mean = sum(values) / len(values)
        stdev = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        self._since_last_read = [irr]
        body_t = 24 + 2 * math.sin(time.monotonic() / 600) + 0.3 * self.address
        scale = 1
        return {
            reg.IO_DEVICE_TYPE: 636,
            reg.IO_DATAMODEL_VERSION: 108,
            reg.IO_OPERATIONAL_MODE: reg.MODE_NORMAL,
            reg.IO_STATUS_FLAGS: self.status,
            reg.IO_SCALE_FACTOR: scale,
            reg.IO_SENSOR1_DATA: round(irr * 10) & 0xFFFF,
            reg.IO_RAW_SENSOR1_DATA: round(irr * 1.004 * 10) & 0xFFFF,
            reg.IO_STDEV_SENSOR1: round(stdev * 10),
            reg.IO_BODY_TEMPERATURE: round(body_t * 10),
            reg.IO_EXT_POWER_SENSOR: 120,
            reg.IO_RH: 52,
            reg.IO_PRESSURE: 10131,
            reg.IO_BATCH_NUMBER: 25,
            reg.IO_SERIAL_NUMBER: 1000 + self.address,
            reg.IO_SOFTWARE_VERSION: 110,
            reg.IO_HARDWARE_VERSION: 3,
            reg.IO_NODE_ID: self.address,
        }


class SimulatedBus:
    def __init__(
        self,
        addresses: list[int],
        baudrate: int = reg.DEFAULT_BAUDRATE,
        timeout: float = 0.3,
        failure_rate: float = 0.003,
        seed: int | None = None,
    ) -> None:
        self.description = f"SIMULADO ({baudrate} baud 8E1)"
        self.baudrate = baudrate
        self.timeout = timeout
        self.failure_rate = failure_rate
        self.rng = random.Random(seed)
        sky = _Sky(self.rng)
        self.sensors = {a: _FakeSMP(a, sky, self.rng) for a in addresses}

    def _no_response(self, slave: int) -> BusError:
        time.sleep(self.timeout)
        return BusError(f"NoResponseError: sin respuesta del esclavo {slave} (simulado)")

    def read_input_registers(self, slave: int, start: int, count: int) -> list[int]:
        sensor = self.sensors.get(slave)
        if sensor is None or self.rng.random() < self.failure_rate:
            raise self._no_response(slave)
        time.sleep(read_transaction_wire_time_s(count, self.baudrate) + TURNAROUND_S)
        values = sensor.registers()
        return [values.get(start + i, 0) for i in range(count)]

    def write_coil(self, slave: int, coil: int, value: bool) -> None:
        sensor = self.sensors.get(slave)
        if sensor is None:
            raise self._no_response(slave)
        time.sleep(read_transaction_wire_time_s(0, self.baudrate) + TURNAROUND_S)
        if coil == reg.IO_CLEAR_ERROR and value:
            sensor.status &= ~reg.CLEARABLE_MASK

    def reopen(self) -> None:
        pass

    def close(self) -> None:
        pass

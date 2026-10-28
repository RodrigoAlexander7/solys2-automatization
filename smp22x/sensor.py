"""Lectura e interpretación de un piranómetro SMP10x/SMP22x en el bus."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import registers as reg
from .transport import BusError, ModbusBus


def to_s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def scale_sensor_value(raw: int, scale_factor: int) -> float | None:
    """Aplica IO_SCALE_FACTOR (§15.6) a IO_SENSOR1_DATA / IO_RAW_SENSOR1_DATA.

    Nota: el programa en C del manual (§15.11) omite los `break` del switch y,
    tal como está escrito, siempre devolvería 0.0; aquí se usa una tabla.
    """
    divisor = reg.SCALE_DIVISORS.get(scale_factor)
    if divisor is None:
        return None
    return to_s16(raw) / divisor


def pressure_to_hpa(raw: int) -> float:
    """Convierte IO_PRESSURE a hPa.

    El manual V20 es contradictorio: la tabla §15.2 dice "en hPa" y el detalle
    §15.6 dice "en 0.1 hPa". La presión interna de un sensor sellado está en
    torno a 500-1100 hPa, así que un valor bruto > 2000 solo puede estar en
    décimas de hPa.
    """
    return raw / 10.0 if raw > 2000 else float(raw)


@dataclass
class DeviceInfo:
    address: int
    device_type: int
    model: str
    datamodel_version: int
    batch_year: int
    serial_number: int
    software_version: int
    hardware_version: int
    node_id: int
    warnings: list[str] = field(default_factory=list)

    @property
    def serial_label(self) -> str:
        # §15.10: solo la combinación lote + nº de serie es única
        return f"{self.batch_year:02d}{self.serial_number:04d}"


@dataclass
class Sample:
    address: int
    time_utc: datetime
    ok: bool
    error: str = ""
    mode: int | None = None
    status: int | None = None
    scale_factor: int | None = None
    irradiance: float | None = None        # W/m², IO_SENSOR1_DATA
    raw_irradiance: float | None = None    # W/m², IO_RAW_SENSOR1_DATA
    stdev: float | None = None             # W/m², IO_STDEV_SENSOR1
    body_temp_c: float | None = None
    supply_v: float | None = None
    rh_pct: float | None = None
    pressure_hpa: float | None = None
    rtt_ms: float | None = None

    @property
    def valid(self) -> bool:
        return (
            self.ok
            and self.irradiance is not None
            and self.mode == reg.MODE_NORMAL
            and self.status is not None
            and not (self.status & reg.INVALID_DATA_MASK)
        )

    @property
    def status_names(self) -> list[str]:
        return reg.decode_status(self.status) if self.status else []


class SMPSensor:
    def __init__(self, bus: ModbusBus, address: int) -> None:
        if not 1 <= address <= 247:
            raise ValueError(f"Dirección Modbus fuera de rango: {address}")
        self.bus = bus
        self.address = address

    def read_info(self) -> DeviceInfo:
        head = self.bus.read_input_registers(self.address, reg.IO_DEVICE_TYPE, 2)
        ident = self.bus.read_input_registers(self.address, reg.IO_BATCH_NUMBER, 5)
        device_type, datamodel = head
        info = DeviceInfo(
            address=self.address,
            device_type=device_type,
            model=reg.DEVICE_TYPES.get(device_type, f"desconocido ({device_type})"),
            datamodel_version=datamodel,
            batch_year=ident[0],
            serial_number=ident[1],
            software_version=ident[2],
            hardware_version=ident[3],
            node_id=ident[4],
        )
        if device_type not in reg.DEVICE_TYPES:
            info.warnings.append(f"IO_DEVICE_TYPE={device_type} no es un SMP10x/SMP22x")
        if datamodel < reg.MIN_DATAMODEL_VERSION:
            info.warnings.append(
                f"data model {datamodel} < {reg.MIN_DATAMODEL_VERSION}: el mapa de "
                "registros puede diferir del manual V20"
            )
        if info.node_id != self.address:
            info.warnings.append(f"IO_NODE_ID={info.node_id} no coincide con la dirección consultada")
        return info

    def read_sample(self, with_diagnostics: bool = False) -> Sample:
        t0 = time.perf_counter()
        try:
            regs = self.bus.read_input_registers(
                self.address, reg.FAST_BLOCK_START, reg.FAST_BLOCK_COUNT
            )
        except BusError as exc:
            return Sample(self.address, datetime.now(timezone.utc), ok=False, error=str(exc))
        rtt_ms = (time.perf_counter() - t0) * 1000.0
        now = datetime.now(timezone.utc)

        mode, status, scale_raw, s1, raw1, stdev, body_t, ext_v = regs
        scale = to_s16(scale_raw)
        sample = Sample(
            address=self.address,
            time_utc=now,
            ok=True,
            mode=mode,
            status=status,
            scale_factor=scale,
            irradiance=scale_sensor_value(s1, scale),
            raw_irradiance=scale_sensor_value(raw1, scale),
            stdev=to_s16(stdev) / 10.0,
            body_temp_c=to_s16(body_t) / 10.0,
            supply_v=to_s16(ext_v) / 10.0,
            rtt_ms=rtt_ms,
        )
        if sample.irradiance is None:
            sample.error = f"IO_SCALE_FACTOR={scale} no documentado"

        if with_diagnostics:
            try:
                rh, pressure = self.bus.read_input_registers(self.address, reg.IO_RH, 2)
                sample.rh_pct = rh / 10.0
                sample.pressure_hpa = pressure_to_hpa(pressure)
            except BusError as exc:
                sample.error = f"diagnóstico: {exc}"
        return sample

    def clear_error(self) -> None:
        """Escribe IO_CLEAR_ERROR (coil 10). §15.8: tras cada arranque el sensor
        levanta IO_ERROR_FLAG y el maestro debe borrarlo; no modifica la
        configuración guardada."""
        self.bus.write_coil(self.address, reg.IO_CLEAR_ERROR, True)

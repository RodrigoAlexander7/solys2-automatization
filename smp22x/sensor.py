"""Lectura e interpretación de un sensor smart de Kipp & Zonen en el bus:
piranómetro SMP10x/SMP22x o pirgeómetro SGR4 (mismo mapa de registros, §15.2)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import registers as reg
from .transport import BusError, ModbusBus, PortError
from .tracing import emit, traced
from typing import Literal

SampleError = Literal["communication_error", "invalid_register_block", "unsupported_scale", "diagnostic_error"]


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


# Diferencia admitida entre Ld↓ leído (IO_SENSOR2_DATA) y Ld↓ calculado como
# neta + σT⁴. Cubre la resolución de 1 W/m² con escala 0 y la de 0,1 °C de la
# temperatura (≈ 0,6 W/m²). Si se supera, el firmware probablemente no sigue el
# mapa de registros 10–11 del manual V20.
LW_CHECK_TOLERANCE_WM2 = 5.0


def blackbody_wm2(temp_c: float) -> float:
    """Emisión de cuerpo negro del detector, σ·T⁴, a la temperatura del cuerpo."""
    return reg.STEFAN_BOLTZMANN * (temp_c + 273.15) ** 4


@dataclass
class DeviceInfo:
    address: int
    sensor_type: str
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
    sensor_type: str = reg.PYRANOMETER
    mode: int | None = None
    status: int | None = None
    scale_factor: int | None = None
    irradiance: float | None = None        # W/m², IO_SENSOR1_DATA (neta en el pirgeómetro)
    raw_irradiance: float | None = None    # W/m², IO_RAW_SENSOR1_DATA
    lw_down: float | None = None           # W/m², IO_SENSOR2_DATA (solo pirgeómetro)
    raw_lw_down: float | None = None       # W/m², IO_RAW_SENSOR2_DATA (solo pirgeómetro)
    stdev: float | None = None             # W/m², IO_STDEV_SENSOR1
    body_temp_c: float | None = None
    supply_v: float | None = None
    rh_pct: float | None = None
    pressure_hpa: float | None = None
    rtt_ms: float | None = None
    error_code: SampleError | None = None
    raw_registers: dict[int, int] = field(default_factory=dict)

    @property
    def measurement(self) -> float | None:
        """Magnitud principal del sensor: irradiancia global (piranómetro) o Ld↓ (pirgeómetro)."""
        return self.lw_down if self.sensor_type == reg.PYRGEOMETER else self.irradiance

    @property
    def lw_down_calc(self) -> float | None:
        """Ld↓ calculado con la fórmula del manual SGR4 §7.1.5: neta + σT⁴.

        Sirve para comprobar en el hardware que IO_SENSOR2_DATA es realmente Ld↓.
        """
        if self.sensor_type != reg.PYRGEOMETER or self.irradiance is None or self.body_temp_c is None:
            return None
        return self.irradiance + blackbody_wm2(self.body_temp_c)

    @property
    def valid(self) -> bool:
        return (
            self.ok
            and self.measurement is not None
            and self.mode == reg.MODE_NORMAL
            and self.status is not None
            and not (self.status & reg.INVALID_DATA_MASK)
        )

    @property
    def status_names(self) -> list[str]:
        return reg.decode_status(self.status) if self.status else []


class SMPSensor:
    """Un sensor smart en una dirección del bus. Aunque se llama SMPSensor, sirve
    también para el pirgeómetro SGR4: cambia el bloque leído y la magnitud principal."""

    def __init__(self, bus: ModbusBus, address: int, sensor_type: str = reg.PYRANOMETER) -> None:
        if not 1 <= address <= 247:
            raise ValueError(f"Dirección Modbus fuera de rango: {address}")
        if sensor_type not in reg.SENSOR_LABELS:
            raise ValueError(f"Tipo de sensor desconocido: {sensor_type}")
        self.bus = bus
        self.address = address
        self.sensor_type = sensor_type

    @property
    def block_count(self) -> int:
        if self.sensor_type == reg.PYRGEOMETER:
            return reg.PYRGEOMETER_BLOCK_COUNT
        return reg.FAST_BLOCK_COUNT

    def read_info(self) -> DeviceInfo:
        head = self.bus.read_input_registers(self.address, reg.IO_DEVICE_TYPE, 2)
        ident = self.bus.read_input_registers(self.address, reg.IO_BATCH_NUMBER, 5)
        device_type, datamodel = head
        model = reg.DEVICE_TYPES.get(device_type) or reg.OBSERVED_DEVICE_TYPES.get(device_type)
        info = DeviceInfo(
            address=self.address,
            sensor_type=self.sensor_type,
            device_type=device_type,
            model=model or f"desconocido ({device_type})",
            datamodel_version=datamodel,
            batch_year=ident[0],
            serial_number=ident[1],
            software_version=ident[2],
            hardware_version=ident[3],
            node_id=ident[4],
        )
        if device_type in reg.OBSERVED_DEVICE_TYPES:
            info.warnings.append(
                f"IO_DEVICE_TYPE={device_type} no figura en el manual V20; "
                f"se asume {model} por la prueba 01"
            )
        elif device_type not in reg.DEVICE_TYPES:
            info.warnings.append(f"IO_DEVICE_TYPE={device_type} no es un modelo conocido")
        expected = reg.DEVICE_SENSOR_TYPES.get(device_type)
        if expected is not None and expected != self.sensor_type:
            info.warnings.append(
                f"el modelo {model} es un {reg.SENSOR_LABELS[expected]}, pero la dirección "
                f"{self.address} está configurada como {reg.SENSOR_LABELS[self.sensor_type]} "
                "(revise --pyrgeometers)"
            )
        if datamodel < reg.MIN_DATAMODEL_VERSION:
            info.warnings.append(
                f"data model {datamodel} < {reg.MIN_DATAMODEL_VERSION}: el mapa de "
                "registros puede diferir del manual V20"
            )
        if info.node_id != self.address:
            info.warnings.append(f"IO_NODE_ID={info.node_id} no coincide con la dirección consultada")
        return info

    @traced("SMPSensor.read_sample", expected=(PortError,))
    def read_sample(self, with_diagnostics: bool = False) -> Sample:
        t0 = time.perf_counter()
        try:
            regs = self.bus.read_input_registers(
                self.address, reg.FAST_BLOCK_START, self.block_count
            )
        except BusError as exc:
            emit("Modbus.read", "FAILED", (time.perf_counter() - t0) * 1000,
                 address=self.address, error="communication_error", message=str(exc))
            return Sample(
                self.address, datetime.now(timezone.utc), ok=False, error=str(exc),
                sensor_type=self.sensor_type, error_code="communication_error",
            )
        rtt_ms = (time.perf_counter() - t0) * 1000.0
        now = datetime.now(timezone.utc)
        if len(regs) != self.block_count or any(type(x) is not int or not 0 <= x <= 65535 for x in regs):
            return Sample(self.address, now, ok=False, sensor_type=self.sensor_type,
                          error="Bloque Modbus incompleto o fuera de U16", error_code="invalid_register_block")

        mode, status, scale_raw, s1, raw1, stdev, body_t, ext_v = regs[:reg.FAST_BLOCK_COUNT]
        scale = to_s16(scale_raw)
        sample = Sample(
            address=self.address,
            time_utc=now,
            ok=True,
            sensor_type=self.sensor_type,
            mode=mode,
            status=status,
            scale_factor=scale,
            irradiance=scale_sensor_value(s1, scale),
            raw_irradiance=scale_sensor_value(raw1, scale),
            stdev=to_s16(stdev) / 10.0,
            body_temp_c=to_s16(body_t) / 10.0,
            supply_v=to_s16(ext_v) / 10.0,
            rtt_ms=rtt_ms,
            raw_registers={reg.FAST_BLOCK_START + i: value for i, value in enumerate(regs)},
        )
        if self.sensor_type == reg.PYRGEOMETER:
            s2, raw2 = regs[reg.FAST_BLOCK_COUNT:]
            sample.lw_down = scale_sensor_value(s2, scale)
            sample.raw_lw_down = scale_sensor_value(raw2, scale)
        if sample.irradiance is None:
            sample.error = f"IO_SCALE_FACTOR={scale} no documentado"
            sample.error_code = "unsupported_scale"

        if with_diagnostics:
            try:
                rh, pressure = self.bus.read_input_registers(self.address, reg.IO_RH, 2)
                sample.rh_pct = rh / 10.0
                sample.pressure_hpa = pressure_to_hpa(pressure)
                sample.raw_registers.update({reg.IO_RH: rh, reg.IO_PRESSURE: pressure})
            except BusError as exc:
                # Se añade al error de escala, si lo hay, en vez de taparlo
                sample.error = "; ".join(filter(None, [sample.error, f"diagnóstico: {exc}"]))
                sample.error_code = sample.error_code or "diagnostic_error"
        return sample

    def clear_error(self) -> None:
        """Escribe IO_CLEAR_ERROR (coil 10). §15.8: tras cada arranque el sensor
        levanta IO_ERROR_FLAG y el maestro debe borrarlo; no modifica la
        configuración guardada."""
        self.bus.write_coil(self.address, reg.IO_CLEAR_ERROR, True)

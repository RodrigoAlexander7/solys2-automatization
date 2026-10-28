"""Transporte Modbus RTU sobre el puerto COM virtual del PMU485.

El PMU485 es un conversor USB <-> RS-485 de 2 hilos con chip FTDI: en Windows
aparece como un puerto COM normal. Modbus RTU es maestro/esclavo y half-duplex,
así que todas las peticiones a los tres sensores pasan en serie por el mismo
objeto de puerto; nunca se deben lanzar peticiones concurrentes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import minimalmodbus
import serial
from serial.tools import list_ports

from . import registers as reg

FTDI_VID = 0x0403


class BusError(Exception):
    """Fallo Modbus de una transacción concreta (sin respuesta, CRC, excepción)."""


class PortError(Exception):
    """El puerto COM no está disponible (USB desconectado, puerto ocupado...)."""


class ModbusBus(Protocol):
    description: str
    baudrate: int
    parity: str

    def read_input_registers(self, slave: int, start: int, count: int) -> list[int]: ...

    def write_coil(self, slave: int, coil: int, value: bool) -> None: ...

    def reopen(self) -> None: ...

    def close(self) -> None: ...


@dataclass
class PortInfo:
    device: str
    description: str
    is_ftdi: bool


def available_ports() -> list[PortInfo]:
    return [
        PortInfo(p.device, p.description or "", p.vid == FTDI_VID)
        for p in sorted(list_ports.comports(), key=lambda p: p.device)
    ]


def autodetect_port() -> str:
    """Elige el puerto del PMU485: el único FTDI, o el único puerto existente."""
    ports = available_ports()
    ftdi = [p for p in ports if p.is_ftdi]
    if len(ftdi) == 1:
        return ftdi[0].device
    if len(ports) == 1:
        return ports[0].device
    if not ports:
        raise PortError(
            "No se encontró ningún puerto COM. Conecte el PMU485 por USB y "
            "compruebe en el Administrador de dispositivos que aparece como "
            "'USB Serial Port (COMx)'."
        )
    listing = ", ".join(f"{p.device} ({p.description})" for p in ports)
    raise PortError(f"Hay varios puertos COM; indique uno con --port. Encontrados: {listing}")


def bits_per_char(parity: str = reg.DEFAULT_PARITY) -> int:
    """Bits de un carácter en la línea: 1 start + 8 datos + paridad (0 o 1) + 1 stop.

    SerialBus abre el puerto siempre con 1 bit de parada, así que sin paridad
    son 10 bits y no 11.
    """
    return 10 if parity == "N" else 11


def frame_time_s(n_bytes: int, baudrate: int, parity: str = reg.DEFAULT_PARITY) -> float:
    return n_bytes * bits_per_char(parity) / baudrate


def read_transaction_wire_time_s(
    n_registers: int, baudrate: int, parity: str = reg.DEFAULT_PARITY
) -> float:
    """Tiempo mínimo en el cable de una lectura FC04: petición + respuesta + 2 silencios t3.5.

    Petición: dirección + función + inicio(2) + cantidad(2) + CRC(2) = 8 bytes.
    Respuesta: dirección + función + nº bytes + 2*N + CRC(2) = 5 + 2N bytes.
    """
    silent = max(3.5 * bits_per_char(parity) / baudrate, 0.00175)
    return (
        frame_time_s(8, baudrate, parity)
        + frame_time_s(5 + 2 * n_registers, baudrate, parity)
        + 2 * silent
    )


class SerialBus:
    """Bus RS-485 real a través de minimalmodbus sobre un único serial.Serial."""

    def __init__(
        self,
        port: str,
        baudrate: int = reg.DEFAULT_BAUDRATE,
        parity: str = reg.DEFAULT_PARITY,
        timeout: float = 0.3,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.timeout = timeout
        self.description = f"{port} {baudrate} baud 8{parity}1"
        self._serial: serial.Serial | None = None
        self._inst: minimalmodbus.Instrument | None = None
        self.reopen()

    def reopen(self) -> None:
        self.close()
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                parity=self.parity,
                bytesize=reg.DEFAULT_BYTESIZE,
                stopbits=reg.DEFAULT_STOPBITS,
                timeout=self.timeout,
                write_timeout=1.0,
            )
        except serial.SerialException as exc:
            raise PortError(f"No se pudo abrir {self.port}: {exc}") from exc
        self._inst = minimalmodbus.Instrument(self._serial, 1)
        self._inst.clear_buffers_before_each_transaction = True

    def _instrument(self, slave: int) -> minimalmodbus.Instrument:
        if self._inst is None:
            raise PortError(f"El puerto {self.port} está cerrado")
        self._inst.address = slave
        return self._inst

    def read_input_registers(self, slave: int, start: int, count: int) -> list[int]:
        inst = self._instrument(slave)
        try:
            return inst.read_registers(start, count, functioncode=reg.FC_READ_INPUT_REGISTERS)
        except minimalmodbus.ModbusException as exc:
            raise BusError(f"{type(exc).__name__}: {exc}") from exc
        except serial.SerialException as exc:
            raise PortError(f"{self.port}: {exc}") from exc

    def write_coil(self, slave: int, coil: int, value: bool) -> None:
        inst = self._instrument(slave)
        try:
            inst.write_bit(coil, int(value), functioncode=reg.FC_WRITE_SINGLE_COIL)
        except minimalmodbus.ModbusException as exc:
            raise BusError(f"{type(exc).__name__}: {exc}") from exc
        except serial.SerialException as exc:
            raise PortError(f"{self.port}: {exc}") from exc

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except serial.SerialException:
                pass
        self._serial = None
        self._inst = None

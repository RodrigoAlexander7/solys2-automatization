"""Mapa Modbus de los piranómetros Kipp & Zonen SMP10x / SMP22x.

Fuente: doc/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf,
apéndice 15 (secciones citadas en cada bloque). Todas las direcciones son
direcciones PDU (base 0), que es lo que se envía en la trama Modbus RTU.
"""

# §15.1 Funciones Modbus implementadas por el sensor
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05

# §8.1 Parámetros de comunicación de fábrica
DEFAULT_BAUDRATE = 19200
DEFAULT_PARITY = "E"
DEFAULT_BYTESIZE = 8
DEFAULT_STOPBITS = 1

# §15.2 / §15.6 Input registers (solo lectura)
IO_DEVICE_TYPE = 0
IO_DATAMODEL_VERSION = 1
IO_OPERATIONAL_MODE = 2
IO_STATUS_FLAGS = 3
IO_SCALE_FACTOR = 4
IO_SENSOR1_DATA = 5        # irradiancia compensada en temperatura, W/m² (S16 escalado)
IO_RAW_SENSOR1_DATA = 6    # irradiancia sin linealizar ni compensar (S16 escalado)
IO_STDEV_SENSOR1 = 7       # desviación estándar desde la lectura anterior, 0.1 W/m²
IO_BODY_TEMPERATURE = 8    # 0.1 °C
IO_EXT_POWER_SENSOR = 9    # tensión de alimentación, 0.1 V
IO_RH = 16                 # humedad relativa interna, 0.1 %
IO_PRESSURE = 17           # presión interna (unidad ambigua en el manual, ver sensor.py)
IO_ERROR_CODE = 26
IO_BATCH_NUMBER = 41       # año de fabricación, YY
IO_SERIAL_NUMBER = 42
IO_SOFTWARE_VERSION = 43
IO_HARDWARE_VERSION = 44
IO_NODE_ID = 45            # dirección Modbus actual

# Bloque de lectura rápida: registros 2..9 en una sola transacción. Es el mismo
# bloque que usa el programa de ejemplo del manual (§15.11), ampliado hasta la
# tensión de alimentación.
FAST_BLOCK_START = IO_OPERATIONAL_MODE
FAST_BLOCK_COUNT = IO_EXT_POWER_SENSOR - IO_OPERATIONAL_MODE + 1  # 8 registros

# §15.5 / §15.9 Coils
IO_CLEAR_ERROR = 10

# §15.6 IO_DEVICE_TYPE
DEVICE_TYPES = {635: "SMP10x", 636: "SMP22x"}

# §15.6 IO_DATAMODEL_VERSION: el manual V20 es válido para data model >= 108
MIN_DATAMODEL_VERSION = 108

# §15.6 IO_OPERATIONAL_MODE
MODE_NORMAL = 1
OPERATIONAL_MODES = {
    1: "normal",
    2: "service",
    3: "calibration",
    4: "factory",
    5: "error",
}

# §15.6 IO_STATUS_FLAGS (bit -> nombre)
STATUS_BITS = {
    0: "VOID_DATA",
    1: "OVERFLOW",
    2: "UNDERFLOW",
    3: "ERROR",
    4: "ADC_ERROR",
    5: "DAC_ERROR",
    6: "CALIBRATION_ERROR",
    7: "UPDATE_FAILED",
    8: "POWER_FAILED",
    10: "RH_ERROR",
    11: "DESICCANT_WARNING",
    12: "BODY_TEMP_ERROR",
}

# Bits que invalidan la muestra de irradiancia. ERROR (bit 3) y POWER_FAILED
# (bit 8) se activan siempre tras un arranque (§15.8, IO_ERROR_FLAG) y no
# implican por sí solos un dato malo; DESICCANT_WARNING es aviso de
# mantenimiento. Un error del sensor de temperatura sí invalida el dato porque
# la compensación térmica depende de él.
INVALID_DATA_MASK = (
    (1 << 0) | (1 << 1) | (1 << 2) | (1 << 4) | (1 << 6) | (1 << 12)
)

# Bits que justifican escribir IO_CLEAR_ERROR (errores no fatales recuperables).
CLEARABLE_MASK = (1 << 3) | (1 << 4) | (1 << 5) | (1 << 8)

# §15.6 IO_SCALE_FACTOR -> divisor aplicado a los registros IO_SENSOR*_DATA
SCALE_DIVISORS = {2: 100.0, 1: 10.0, 0: 1.0, -1: 0.1}


def decode_status(flags: int) -> list[str]:
    """Devuelve los nombres de los bits activos de IO_STATUS_FLAGS."""
    return [name for bit, name in STATUS_BITS.items() if flags & (1 << bit)]

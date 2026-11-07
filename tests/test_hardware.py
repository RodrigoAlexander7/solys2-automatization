"""Pruebas con el bus real (R27, nivel hardware). Se omiten salvo SMP_HARDWARE=1.

Uso, con la adquisición y SmartExplorer cerrados:

    SMP_HARDWARE=1 SMP_SERIAL_PORT=COM3 .venv/Scripts/python -m unittest discover -s tests -p test_hardware.py

Guardan un volcado de registros por dirección en tests/fixtures/hardware/ para
usarlo después como caso de conversión fijo. El resultado se anota a mano en
docs/sdd/tasks.md: es evidencia externa, no la certifica el simulador.
"""
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from smp22x import registers as reg

HARDWARE = os.environ.get("SMP_HARDWARE") == "1"
FIXTURES = Path(__file__).parent / "fixtures" / "hardware"


@unittest.skipUnless(HARDWARE, "SMP_HARDWARE=1 para probar con el bus real")
class HardwareBusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from smp22x.transport import SerialBus, autodetect_port
        port = os.environ.get("SMP_SERIAL_PORT", "auto")
        cls.bus = SerialBus(autodetect_port() if port == "auto" else port)
        cls.addresses = [int(a) for a in os.environ.get("SMP_ADDRESSES", "2 3 4").split()]
        cls.pyrgeometers = [int(a) for a in os.environ.get("SMP_PYRGEOMETERS", "2").split()]

    @classmethod
    def tearDownClass(cls):
        cls.bus.close()

    def sensor(self, address):
        from smp22x.sensor import SMPSensor
        kind = reg.PYRGEOMETER if address in self.pyrgeometers else reg.PYRANOMETER
        return SMPSensor(self.bus, address, kind)

    def test_every_configured_address_identifies_itself(self):
        for address in self.addresses:
            info = self.sensor(address).read_info()
            self.assertEqual(info.node_id, address)

    def test_readings_are_valid_and_dumped(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for address in self.addresses:
            sensor = self.sensor(address)
            sensor.clear_error()
            sample = sensor.read_sample(with_diagnostics=True)
            self.assertTrue(sample.ok, sample.error)
            info = sensor.read_info()
            dump = {"captured_utc": datetime.now(timezone.utc).isoformat(), "address": address,
                    "sensor_type": sensor.sensor_type, "device_type": info.device_type,
                    "datamodel": info.datamodel_version, "registers": sample.raw_registers,
                    "converted": {"measurement": sample.measurement, "irradiance": sample.irradiance,
                                  "lw_down": sample.lw_down, "body_temp_c": sample.body_temp_c}}
            (FIXTURES / f"address_{address}.json").write_text(json.dumps(dump, indent=2), encoding="utf-8")

    def test_pyrgeometer_register_map(self):
        """Confirma en el SGR4 real que el registro 10 es Ld↓ (neta + σT⁴ dentro de la tolerancia)."""
        from smp22x.sensor import LW_CHECK_TOLERANCE_WM2
        for address in self.pyrgeometers:
            sample = self.sensor(address).read_sample()
            self.assertTrue(sample.ok, sample.error)
            self.assertLessEqual(abs(sample.lw_down - sample.lw_down_calc), LW_CHECK_TOLERANCE_WM2)


class HardwareFixtureTests(unittest.TestCase):
    """Los volcados guardados se reconvierten igual cada vez (si existen)."""

    def test_saved_dumps_reconvert(self):
        from smp22x.sensor import scale_sensor_value
        dumps = sorted(FIXTURES.glob("address_*.json")) if FIXTURES.exists() else []
        if not dumps:
            self.skipTest("sin volcados de hardware todavía")
        for path in dumps:
            data = json.loads(path.read_text(encoding="utf-8"))
            regs = {int(k): v for k, v in data["registers"].items()}
            value = scale_sensor_value(regs[5], regs[4] - 0x10000 if regs[4] & 0x8000 else regs[4])
            self.assertEqual(value, data["converted"]["irradiance"], path.name)


if __name__ == "__main__":
    unittest.main()

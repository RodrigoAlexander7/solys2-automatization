"""Sincronización del reloj (R14; C12): SNTP, eventos y avisos. Nunca ajusta el reloj."""
import contextlib
import io
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from smp22x.acquisition import Acquisition, AcquisitionConfig
from smp22x.api import create_app
from smp22x.clock import (NTP_EPOCH_OFFSET, PACKET, ClockCheck, ClockMonitor, build_request, clock_event,
                          parse_response, sntp_check)
from smp22x.config import Settings
from smp22x.result import Ok, require
from smp22x.storage import Reader
from test_smp22x import FakeBus, fast_regs


def ntp(t: float) -> tuple[int, int]:
    s = t + NTP_EPOCH_OFFSET
    return int(s), int((s - int(s)) * 2 ** 32)


def response(request: bytes, offset_s: float, mode: int = 4, stratum: int = 2, echo: bool = True) -> bytes:
    words = list(PACKET.unpack(request))[4:]
    now = time.time() + offset_s
    out = [0] * 11
    if echo:
        out[5], out[6] = words[9], words[10]  # originate = transmit del cliente
    out[7], out[8] = ntp(now)
    out[9], out[10] = ntp(now + 0.0005)
    return PACKET.pack((4 << 3) | mode, stratum, 0, 0, *out)


class FakeNtpServer:
    def __init__(self, offset_s=0.0, **kwargs):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.offset_s, self.kwargs = offset_s, kwargs
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            data, address = self.sock.recvfrom(512)
            self.sock.sendto(response(data, self.offset_s, **self.kwargs), address)
        except OSError:
            pass

    def close(self):
        self.sock.close()


class SntpTests(unittest.TestCase):
    def check(self, **server_kwargs) -> ClockCheck:
        server = FakeNtpServer(**server_kwargs)
        self.addCleanup(server.close)
        return sntp_check("127.0.0.1", port=server.port, timeout=2)

    def test_offset_is_measured(self):
        result = self.check(offset_s=2.5)
        self.assertTrue(result.ok, result.error)
        self.assertAlmostEqual(result.offset_s, 2.5, delta=0.05)
        self.assertGreaterEqual(result.delay_s, -0.01)
        self.assertEqual(result.stratum, 2)

    def test_invalid_responses_are_rejected(self):
        for kwargs, fragment in (({"mode": 3}, "modo"), ({"stratum": 0}, "estrato"), ({"echo": False}, "no corresponde")):
            result = self.check(**kwargs)
            self.assertFalse(result.ok)
            self.assertIn(fragment, result.error)

    def test_unreachable_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:  # nadie responde en ese puerto
            result = sntp_check("127.0.0.1", port=port, timeout=0.3)
        finally:
            sock.close()
        self.assertFalse(result.ok)
        self.assertTrue(result.error)

    def test_request_packet(self):
        packet = build_request(1_700_000_000.25)
        first = packet[0]
        self.assertEqual((first & 0x7, (first >> 3) & 0x7), (3, 4))
        self.assertEqual(len(packet), 48)
        with self.assertRaises(ValueError):
            parse_response(b"short", 0, 0)

    def test_events_and_threshold(self):
        ok = clock_event(ClockCheck(1, "s", True, 0.2, 0.01, 2), 1.0)
        far = clock_event(ClockCheck(1, "s", True, -3.0, 0.01, 2), 1.0)
        down = clock_event(ClockCheck(1, "s", False, error="timeout"), 1.0)
        self.assertEqual((ok.kind, ok.severity), ("clock_check", "info"))
        self.assertEqual((far.kind, far.severity), ("clock_check", "warning"))
        self.assertEqual((down.kind, down.severity), ("clock_unavailable", "warning"))

    def test_monitor_never_touches_the_system_clock(self):
        before = time.time()
        monitor = ClockMonitor("x", 0.05, check=lambda server: ClockCheck(int(time.time() * 1000), server, True, 5.0, 0.0, 1))
        monitor.start()
        time.sleep(0.2)
        monitor.stop()
        results = monitor.drain()
        self.assertGreaterEqual(len(results), 2)
        self.assertLess(abs(time.time() - before - 0.2), 0.2)


class AcquisitionClockTests(unittest.TestCase):
    def test_clock_checks_are_recorded_and_warned(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "acq.sqlite3"
            cfg = AcquisitionConfig(addresses=[3], interval_s=0.05, duration_s=0.5, out_dir=Path(directory) / "out",
                                    live=False, db_path=db, ntp_server="ntp.test", clock_check_s=10, clock_max_offset_s=1.0)
            fake = lambda server: ClockCheck(int(time.time() * 1000), server, True, 2.4, 0.02, 2)
            with mock.patch("smp22x.clock.sntp_check", fake), \
                    mock.patch("smp22x.acquisition.ClockMonitor", lambda s, e: ClockMonitor(s, e, check=fake)), \
                    contextlib.redirect_stdout(io.StringIO()):
                acq = Acquisition(FakeBus(fast_regs()), cfg)
                acq.run()
            reader = Reader(db)
            events = [e for e in require(reader.events(0, 2 ** 62)) if e["kind"] == "clock_check"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["severity"], "warning")
            self.assertAlmostEqual(events[0]["detail"]["offset_s"], 2.4)
            run = require(reader.run(acq.run_id))
            self.assertFalse(run["status"]["clock"]["within_limit"])
            # Con la corrida activa, el monitor avisa del desfase.
            run["state"] = "running"
            client = TestClient(create_app(Settings(db=db, frontend=Path(directory), observatory=Path(directory) / "o.db")),
                                base_url="http://127.0.0.1:8050")
            with mock.patch.object(Reader, "run", return_value=Ok(run)):
                body = client.get(f"/api/runs/{acq.run_id}/status").json()
            self.assertIn("se desvía +2.400 s", body["clock"]["warning"])
            self.assertTrue(any("se desvía" in a["text"] for a in body["alerts"]))

    def test_disabled_check_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "acq.sqlite3"
            cfg = AcquisitionConfig(addresses=[3], interval_s=0.05, duration_s=0.2, out_dir=Path(directory) / "out",
                                    live=False, db_path=db, ntp_server="")
            with contextlib.redirect_stdout(io.StringIO()):
                acq = Acquisition(FakeBus(fast_regs()), cfg)
                acq.run()
            status = require(Reader(db).run(acq.run_id))["status"]
            self.assertFalse(status["clock_check_enabled"])
            self.assertIsNone(status["clock"])


if __name__ == "__main__":
    unittest.main()

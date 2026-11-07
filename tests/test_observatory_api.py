"""API del observatorio (R15–R17, R22–R26; C13, C16): configuración, bitácora, seguridad y descargas."""
import csv
import io
import json
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from smp22x.api import create_app
from smp22x.config import Settings, is_loopback, settings
from smp22x.events import iso_ms
from smp22x.simulator import Faults
from scenario import SITE, SUMMER_DAY, simulate

LOCAL = "http://127.0.0.1:8050"
WRITE = {"Origin": LOCAL}
DAY_START = iso_ms(int(SUMMER_DAY.timestamp() * 1000))
DAY_END = iso_ms(int((SUMMER_DAY + timedelta(days=1)).timestamp() * 1000))


def change(**config):
    base = {"site": SITE, "solys2_in_use": True,
            "instruments": [{"address": 2}, {"address": 3, "label": "global"}, {"address": 4, "shaded": True, "label": "difusa"}]}
    base.update(config)
    return {"author": "Ana", "note": "", "valid_from": "2026-06-01T00:00:00Z", "config": base}


class ApiCase(unittest.TestCase):
    seed_hours = 24

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = root / "acq.sqlite3"
        if self.seed_hours:
            # Aviso de desecante en #3: genera un evento dentro del intervalo sin invalidar datos.
            simulate(self.db, hours=self.seed_hours, step_s=300, shaded=(4,), faults={3: Faults(status_bits=1 << 11)})
        self.settings = Settings(db=self.db, frontend=root / "absent", observatory=root / "obs.sqlite3",
                                 archive_dir=root / "archive", references=root / "absent")
        self.client = TestClient(create_app(self.settings), base_url=LOCAL)

    def post(self, path, body, headers=WRITE):
        return self.client.post(path, json=body, headers=headers)


class SecurityTests(ApiCase):
    seed_hours = 0

    def test_foreign_host_is_rejected(self):
        response = self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")
        for host in ("localhost:8050", "127.0.0.1", "[::1]:8050"):
            self.assertEqual(self.client.get("/api/health", headers={"Host": host}).status_code, 200, host)

    def test_dns_rebinding_name_is_rejected(self):
        self.assertEqual(self.client.get("/api/health", headers={"Host": "127.0.0.1.evil.example"}).status_code, 403)

    def test_writes_need_same_origin_and_json(self):
        body = {"author": "Ana", "text": "x"}
        self.assertEqual(self.post("/api/logbook", body, {}).status_code, 403)
        self.assertEqual(self.post("/api/logbook", body, {"Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.post("/api/logbook", body, {"Origin": "http://127.0.0.1:9999"}).status_code, 403)
        form = self.client.post("/api/logbook", content="author=x", headers={**WRITE, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(form.status_code, 415)
        self.assertEqual(self.post("/api/logbook", body).status_code, 200)

    def test_measurement_database_has_no_write_routes(self):
        for path in ("/api/runs", "/api/runs/x/samples", "/api/series", "/api/export"):
            self.assertIn(self.post(path, {}).status_code, (404, 405), path)

    def test_extra_allowed_hosts_from_environment(self):
        cfg = settings({"SMP_ALLOWED_HOSTS": "observatorio.lab"})
        self.assertIn("observatorio.lab", cfg.allowed_hosts)
        self.assertTrue(is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost"))
        self.assertFalse(is_loopback("0.0.0.0") or is_loopback("192.168.1.5"))

    def test_dashboard_refuses_network_binding_without_auth(self):
        from smp22x.cli import build_parser, cmd_dashboard
        args = build_parser().parse_args(["dashboard", "--db", str(self.db)])
        with patch("smp22x.cli.settings", return_value=Settings(host="0.0.0.0")), \
                patch("smp22x.cli.environment", return_value={}), patch("uvicorn.run") as run:
            with self.assertRaises(ValueError):
                cmd_dashboard(args)
            run.assert_not_called()
        with patch("smp22x.cli.settings", return_value=Settings(host="0.0.0.0", container_loopback=True)), \
                patch("smp22x.cli.environment", return_value={}), patch("uvicorn.run") as run:
            cmd_dashboard(args)
            run.assert_called_once()


class ConfigurationTests(ApiCase):
    def test_versions_are_append_only_and_logged(self):
        first = self.post("/api/observatory/config", {**change(), "note": "configuración inicial"})
        self.assertEqual(first.status_code, 200, first.text)
        second = self.post("/api/observatory/config", {**change(solys2_in_use=False), "note": "mantenimiento"})
        self.assertEqual(second.status_code, 200)
        view = self.client.get("/api/observatory/config").json()
        self.assertEqual([v["id"] for v in view["versions"]], [2, 1])
        self.assertFalse(view["current"]["solys2_in_use"])
        entries = self.client.get("/api/logbook", params={"start": "2026-01-01T00:00:00Z"}).json()["items"]
        self.assertTrue(any(e["category"] == "solys2" and "SOLYS2: inactivo" in e["text"] for e in entries))
        self.assertTrue(all(e["source"].startswith("config:") for e in entries))

    def test_retroactive_change_needs_note_and_future_is_rejected(self):
        self.assertEqual(self.post("/api/observatory/config", {**change(), "valid_from": "2026-01-01T00:00:00Z", "note": ""}).status_code, 422)
        self.assertEqual(self.post("/api/observatory/config", {**change(), "valid_from": "2099-01-01T00:00:00Z", "note": "x"}).status_code, 422)
        naive = self.post("/api/observatory/config", {**change(), "valid_from": "2026-06-01T00:00:00", "note": "x"})
        self.assertEqual(naive.status_code, 422)

    def test_invalid_configurations(self):
        bad = [
            change(site={"latitude": 95, "longitude": 0, "altitude_m": 0}),
            change(instruments=[{"address": 3}, {"address": 3}]),
            change(instruments=[{"address": 300}]),
            {**change(), "author": "   "},
        ]
        for body in bad:
            self.assertEqual(self.post("/api/observatory/config", {**body, "note": "x"}).status_code, 422, body)

    def test_warnings_for_suspicious_but_valid_choices(self):
        body = change(site=None, instruments=[{"address": 2, "shaded": True}, {"address": 3}, {"address": 9}])
        warnings = self.post("/api/observatory/config", {**body, "note": "x"}).json()["warnings"]
        text = " ".join(warnings)
        self.assertIn("#9 no figura", text)
        self.assertIn("pirgeómetro", text)
        self.assertIn("no habrá DHI", text)
        self.assertIn("Sin sitio", text)

    def test_roles_follow_configuration_in_api_rows(self):
        self.post("/api/observatory/config", {**change(), "note": "inicial"})
        series = self.client.get("/api/series", params={"start": DAY_START, "end": DAY_END}).json()
        roles = {(i["address"], i["role"]) for i in series["items"]}
        self.assertEqual(roles, {(2, "LWD"), (3, "GHI"), (4, "DHI")})
        self.assertEqual(series["resolution"], "1min")


class LogbookAndTimelineTests(ApiCase):
    def test_entry_void_and_double_void(self):
        entry = self.post("/api/logbook", {"author": "Ana", "category": "limpieza", "addresses": [3, 3, 4],
                                           "text": "Limpieza", "time_utc": "2026-06-21T15:00:00Z"}).json()
        self.assertEqual(entry["addresses"], [3, 4])
        voided = self.post(f"/api/logbook/{entry['id']}/void", {"author": "Luis", "reason": "duplicada"})
        self.assertTrue(voided.json()["voided"])
        self.assertEqual(self.post(f"/api/logbook/{entry['id']}/void", {"author": "Luis", "reason": "otra"}).status_code, 422)
        self.assertEqual(self.post("/api/logbook/999/void", {"author": "Luis", "reason": "x"}).status_code, 404)
        items = self.client.get("/api/logbook", params={"start": DAY_START, "end": DAY_END}).json()["items"]
        self.assertEqual(len(items), 1)  # anulada pero visible
        hidden = self.client.get("/api/logbook", params={"start": DAY_START, "end": DAY_END, "include_voided": False}).json()
        self.assertEqual(hidden["items"], [])

    def test_entry_validation(self):
        for body in ({"author": "", "text": "x"}, {"author": "a", "text": "  "}, {"author": "a", "text": "x", "category": "otra"},
                     {"author": "a", "text": "x" * 2001}, {"author": "a", "text": "x", "time_utc": "2099-01-01T00:00:00Z"}):
            self.assertEqual(self.post("/api/logbook", body).status_code, 422, body)

    def test_timeline_merges_sources_newest_first(self):
        self.post("/api/observatory/config", {**change(), "note": "inicial"})
        self.post("/api/logbook", {"author": "Ana", "text": "Nivelación", "category": "nivelacion", "time_utc": "2026-06-21T10:00:00Z"})
        # El inicio de corrida lleva la hora real del PC: el intervalo llega hasta ahora.
        items = self.client.get("/api/timeline", params={"start": "2026-05-01T00:00:00Z"}).json()["items"]
        sources = {i["source"] for i in items}
        self.assertEqual(sources, {"adquisicion", "bitacora", "configuracion"})
        self.assertEqual([i["t_ms"] for i in items], sorted((i["t_ms"] for i in items), reverse=True))
        self.assertTrue(any(i["kind"] == "run_started" and i["title"].startswith("Inicio de corrida") for i in items))
        only = self.client.get("/api/timeline", params={"start": "2026-05-01T00:00:00Z", "end": "2026-06-23T00:00:00Z",
                                                        "sources": "bitacora"}).json()["items"]
        self.assertTrue(only and all(i["source"] == "bitacora" for i in only))


class ExportTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.post("/api/observatory/config", {**change(), "note": "inicial"})
        self.post("/api/logbook", {"author": "Ana", "text": "Limpieza", "category": "limpieza", "time_utc": "2026-06-21T15:00:00Z"})

    def csv_rows(self, **params):
        response = self.client.get("/api/export", params={"start": DAY_START, "end": DAY_END, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return list(csv.DictReader(io.StringIO(response.text))), response

    def test_one_second_csv_has_all_rows_and_columns(self):
        rows, response = self.csv_rows(resolution="1s", format="csv")
        self.assertIn('attachment; filename="radiometria_', response.headers["content-disposition"])
        self.assertEqual(len(rows), 24 * 12 * 3)
        first = rows[0]
        for column in ("time_utc", "role", "measurement_wm2", "valid", "solar_zenith_deg", "qc_ppl", "qc_summary", "config_version"):
            self.assertIn(column, first)
        self.assertEqual({r["role"] for r in rows}, {"LWD", "GHI", "DHI"})
        self.assertTrue(all(r["config_version"] == "1" for r in rows))

    def test_minute_csv_and_invalid_exclusion(self):
        rows, _ = self.csv_rows(resolution="1min", format="csv")
        self.assertTrue(rows and "n_valid" in rows[0])
        kept, _ = self.csv_rows(resolution="1min", format="csv", invalid="exclude")
        self.assertTrue(all(int(r["n_valid"]) > 0 for r in kept))

    def test_zip_bundle_contents(self):
        response = self.client.get("/api/export", params={"start": DAY_START, "end": DAY_END, "resolution": "1min", "format": "zip"})
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(archive.namelist()), {"datos_1min.csv", "metadata.json", "bitacora.csv", "eventos.csv", "LEEME.txt"})
        meta = json.loads(archive.read("metadata.json"))
        self.assertEqual(meta["query"]["resolution"], "1min")
        self.assertEqual(len(meta["config_versions"]), 1)
        self.assertTrue(meta["runs"] and meta["runs"][0]["simulated"])
        self.assertIn("Long", meta["qc"]["source_bsrn"])
        self.assertIn("Limpieza", archive.read("bitacora.csv").decode())
        self.assertIn("DESICCANT_WARNING", archive.read("eventos.csv").decode())

    def test_limits_and_estimate(self):
        too_long = self.client.get("/api/export", params={"start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "resolution": "1s"})
        self.assertEqual(too_long.status_code, 422)
        inverted = self.client.get("/api/export", params={"start": DAY_END, "end": DAY_START})
        self.assertEqual(inverted.status_code, 422)
        estimate = self.client.get("/api/export/estimate", params={"start": DAY_START, "end": DAY_END, "resolution": "1s"}).json()
        self.assertEqual(estimate["rows"], 24 * 12 * 3)

    def test_figure_is_a_png_generated_on_demand(self):
        response = self.client.get("/api/figure", params={"start": DAY_START, "end": DAY_END})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assertFalse(any(Path(self.tmp.name).rglob("*.png")))  # nada queda en disco

    def test_series_resolution_rules(self):
        short = self.client.get("/api/series", params={"start": DAY_START, "end": iso_ms(int(SUMMER_DAY.timestamp() * 1000) + 3_600_000)}).json()
        self.assertEqual(short["resolution"], "1s")
        forced = self.client.get("/api/series", params={"start": DAY_START, "end": DAY_END, "resolution": "1s"})
        self.assertEqual(forced.status_code, 422)


class LiveStatusTests(ApiCase):
    def test_live_without_runs(self):
        empty = TestClient(create_app(Settings(db=Path(self.tmp.name) / "none.db", frontend=Path(self.tmp.name),
                                               observatory=Path(self.tmp.name) / "o2.db")), base_url=LOCAL)
        body = empty.get("/api/live").json()
        self.assertIsNone(body["run"])
        self.assertEqual(body["latest"], [])

    def test_instruments_inventory(self):
        body = self.client.get("/api/instruments").json()
        self.assertEqual([i["address"] for i in body["items"]], [2, 3, 4])
        self.assertTrue(all(i["latest"] is not None for i in body["items"]))

    def test_live_reports_clock_and_comparisons(self):
        body = self.client.get("/api/live").json()
        self.assertEqual(body["clock"]["warning"], None)  # corrida terminada: sin avisos de reloj
        self.assertEqual(len(body["comparisons"]), 1)  # sin configuración: #3 y #4 son GHI
        self.post("/api/observatory/config", {**change(), "note": "x"})
        body = self.client.get("/api/live").json()
        self.assertEqual(body["comparisons"], [])  # global y difusa no se comparan
        self.assertEqual({s["address"]: s["role"] for s in body["latest"]}, {2: "LWD", 3: "GHI", 4: "DHI"})


if __name__ == "__main__":
    unittest.main()

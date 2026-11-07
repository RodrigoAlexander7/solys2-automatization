"""Disposable real-process fixture for browser tests; never uses production DB.

Siembra las últimas 24 h con el simulador solar (reloj controlado) y luego
arranca una adquisición simulada real y la API en 127.0.0.1:8061. Todo vive en
una carpeta temporal que se borra al terminar.
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def main():
    import uvicorn
    from smp22x.api import create_app
    from smp22x.config import Settings
    port = int(os.environ.get("E2E_PORT", "8061"))
    duration = os.environ.get("E2E_DURATION", "300s")
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "test.db"
        if os.environ.get("E2E_SEED", "1") == "1":
            from scenario import simulate
            # 23 h a 60 s y la última hora a 1 s, como si la adquisición hubiera estado corriendo.
            now = datetime.now(timezone.utc).replace(microsecond=0)
            simulate(db, start=now - timedelta(hours=24), hours=23, step_s=60, shaded=(4,),
                     cloud_mean_gap_s=1800, run_id="historico-simulado")
            simulate(db, start=now - timedelta(hours=1), hours=1, step_s=1, shaded=(4,),
                     cloud_mean_gap_s=300, run_id="ultima-hora-simulada")
        env = {**os.environ, "SMP_LOG_LEVEL": "WARNING", "SMP_ADDRESSES": "2 3 4", "SMP_PYRGEOMETERS": "2",
               "SMP_INTERVAL_S": "1", "SMP_BAUD": "19200", "SMP_PARITY": "E", "SMP_NTP_SERVER": "",
               "SMP_SIM_SHADED": "4", "SMP_SIM_TRACKER": "1"}
        child = subprocess.Popen([sys.executable, "-m", "smp22x", "log", "--simulate", "--db", str(db),
                                  "--out", directory, "--no-plot", "--quiet", "-d", duration,
                                  "--label", "e2e"], cwd=ROOT, env=env)
        try:
            uvicorn.run(create_app(Settings(db=db, frontend=ROOT / "frontend/build",
                                            observatory=Path(directory) / "observatory.sqlite3",
                                            archive_dir=Path(directory) / "archive",
                                            references=ROOT / "references")),
                        host="127.0.0.1", port=port, log_level="warning")
        finally:
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    main()

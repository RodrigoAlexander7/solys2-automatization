"""Disposable real-process fixture for browser tests; never uses production DB."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import uvicorn
    from smp22x.api import create_app
    from smp22x.config import Settings
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "test.db"
        env = {**os.environ, "SMP_LOG_LEVEL": "WARNING", "SMP_ADDRESSES": "2 3 4", "SMP_PYRGEOMETERS": "2",
               "SMP_INTERVAL_S": "1", "SMP_BAUD": "19200", "SMP_PARITY": "E"}
        child = subprocess.Popen([sys.executable, "-m", "smp22x", "log", "--simulate", "--db", str(db),
                                  "--out", directory, "--no-plot", "--quiet", "-d", "120s"], cwd=ROOT, env=env)
        try:
            uvicorn.run(create_app(Settings(db=db, frontend=ROOT / "frontend/build")),
                        host="127.0.0.1", port=8061, log_level="warning")
        finally:
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    main()

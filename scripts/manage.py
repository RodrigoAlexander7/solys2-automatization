"""Portable helpers; usable from PowerShell without GNU Make."""
import argparse
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["env", "run", "help"])
    command = parser.parse_args().command
    if command == "help":
        print("make install | check | test | build | acquire | web | run")
        print("make docker-up | docker-web | docker-down | docker-logs | docker-test")
        print("Windows sin make: python scripts/manage.py env; luego usar .venv\\Scripts\\python -m smp22x")
    elif command == "env":
        if not (ROOT / ".env").exists():
            shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print(".env listo; no se sobrescribe configuración existente.")
    else:
        children = []
        def stop(signum, frame):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, stop)
        try:
            children.append(subprocess.Popen([sys.executable, "-m", "smp22x", "log", "--no-plot"], cwd=ROOT))
            children.append(subprocess.Popen([sys.executable, "-m", "smp22x", "dashboard"], cwd=ROOT))
            while children[0].poll() is None:
                # A web crash must not stop acquisition. Report it once.
                if children[1].poll() is not None and not getattr(main, "reported", False):
                    print("Web terminada; adquisición continúa. Puede reiniciar con make web.")
                    main.reported = True
                time.sleep(.5)
            return children[0].returncode
        except KeyboardInterrupt:
            return 0
        finally:
            for child in children:
                if child.poll() is None:
                    if sys.platform != "win32":
                        child.send_signal(signal.SIGTERM)
                    else:
                        child.terminate()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())

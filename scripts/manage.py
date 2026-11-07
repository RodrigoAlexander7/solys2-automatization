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
    parser.add_argument("command", choices=["env", "run", "run-ligero", "help"])
    command = parser.parse_args().command
    if command == "help":
        print("make install | check | test | test-e2e | test-perf | test-hardware | build | acquire | web | run")
        print("make ventana (interfaz liviana sin navegador) | run-ligero (adquisición + ventana liviana)")
        print("make migrate (esquema 1 → 2, adquisición detenida) | archive (Parquet y retención, adquisición detenida)")
        print("make docker-up | docker-web | docker-down | docker-logs | docker-test")
        print("Windows sin make: python scripts/manage.py env; luego usar .venv\\Scripts\\python -m smp22x")
    elif command == "env":
        if not (ROOT / ".env").exists():
            shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        (ROOT / "data").mkdir(exist_ok=True)
        print(".env listo; no se sobrescribe configuración existente.")
    else:
        children = []
        def stop(signum, frame):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, stop)
        try:
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
            light = command == "run-ligero"
            viewer = ["ventana"] if light else ["dashboard"]
            children.append(subprocess.Popen([sys.executable, "-m", "smp22x", "log", "--no-plot"], cwd=ROOT, **options))
            children.append(subprocess.Popen([sys.executable, "-m", "smp22x", *viewer], cwd=ROOT, **options))
            while children[0].poll() is None:
                # Cerrar la web o la ventana no detiene la adquisición. Se avisa una vez.
                if children[1].poll() is not None and not getattr(main, "reported", False):
                    again = "make ventana" if light else "make web"
                    print(f"{'Ventana cerrada' if light else 'Web terminada'}; la adquisición continúa. "
                          f"Puede volver a abrirla con {again}.")
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
                        child.send_signal(signal.CTRL_BREAK_EVENT)
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())

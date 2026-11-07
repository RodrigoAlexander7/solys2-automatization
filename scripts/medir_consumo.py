"""Mide CPU y memoria de las interfaces (R31). Pensado para correrlo en la mini-PC.

Ejemplos (Windows: .venv\\Scripts\\python):

    # Ventana liviana con datos simulados, durante 2 minutos
    .venv/bin/python scripts/medir_consumo.py --demo --segundos 120

    # Chrome con el dashboard Svelte abierto (suma todos sus procesos)
    .venv/bin/python scripts/medir_consumo.py --proceso chrome --segundos 120

    # Cualquier proceso por PID
    .venv/bin/python scripts/medir_consumo.py --pid 1234 --segundos 60

CPU en % de un núcleo (100 % = un núcleo completo). RAM = memoria residente.
"""
import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HIDDEN_WINDOW = """
import sys, tkinter as tk
from pathlib import Path
from smp22x import ventana
db = Path(sys.argv[1])
root = tk.Tk()
root.withdraw()  # oculta: se mide el trabajo de consulta y dibujo sin mostrar la ventana
w = ventana.LightWindow(root, ventana.LiveSource(db), db, db.with_name("archive"))
w.tick()
root.mainloop()
"""


def processes(args, psutil):
    found = []
    for pid in args.pid or []:
        found.append(psutil.Process(pid))
    for name in args.proceso or []:
        found += [p for p in psutil.process_iter(["name"]) if name.lower() in (p.info["name"] or "").lower()]
    return found


def measure(label: str, get_procs, seconds: float, psutil) -> dict:
    cpu, ram = [], []
    known = {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        procs = get_procs()
        for p in procs:
            if p.pid not in known:
                known[p.pid] = p
                try:
                    p.cpu_percent(None)  # inicia el contador
                except psutil.Error:
                    pass
        time.sleep(1)
        total_cpu = total_ram = 0.0
        for p in list(known.values()):
            try:
                total_cpu += p.cpu_percent(None)
                total_ram += p.memory_info().rss / 1e6
            except psutil.Error:
                known.pop(p.pid, None)
        cpu.append(total_cpu)
        ram.append(total_ram)
    skip = min(5, len(cpu) // 4)  # descartar el arranque
    cpu, ram = cpu[skip:] or cpu, ram[skip:] or ram
    return {"proceso": label, "procesos": len(known), "cpu_media": statistics.fmean(cpu), "cpu_max": max(cpu),
            "ram_media": statistics.fmean(ram), "ram_max": max(ram)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--segundos", type=float, default=60)
    parser.add_argument("--demo", action="store_true", help="adquisición simulada + ventana liviana")
    parser.add_argument("--visible", action="store_true", help="con --demo: mostrar la ventana (por defecto oculta)")
    parser.add_argument("--proceso", nargs="*", help="nombres de proceso a sumar (p. ej. chrome)")
    parser.add_argument("--pid", type=int, nargs="*")
    args = parser.parse_args()
    try:
        import psutil
    except ImportError:
        print("Instale psutil: .venv/bin/python -m pip install psutil")
        return 2
    results, children = [], []
    temp = tempfile.TemporaryDirectory()
    try:
        if args.demo:
            db = Path(temp.name) / "demo.sqlite3"
            env = {**os.environ, "SMP_NTP_SERVER": "", "SMP_LOG_LEVEL": "WARNING"}
            acquisition = subprocess.Popen([sys.executable, "-m", "smp22x", "log", "--simulate", "--db", str(db),
                                            "--out", temp.name, "--no-plot", "--quiet", "--no-archive"],
                                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
            children.append(acquisition)
            while not db.exists():
                time.sleep(0.2)
            time.sleep(3)
            command = ([sys.executable, "-m", "smp22x", "ventana", "--db", str(db)] if args.visible
                       else [sys.executable, "-c", HIDDEN_WINDOW, str(db)])
            window = subprocess.Popen(command, cwd=ROOT, env=env)
            children.append(window)
            print(f"Midiendo {args.segundos:g} s…", flush=True)
            results.append(measure("ventana liviana" + ("" if args.visible else " (oculta)"),
                                   lambda: [psutil.Process(window.pid)], args.segundos, psutil))
            results.append(measure("adquisición simulada", lambda: [psutil.Process(acquisition.pid)], 10, psutil))
        if args.proceso or args.pid:
            print(f"Midiendo {args.segundos:g} s…", flush=True)
            results.append(measure(" + ".join(args.proceso or []) or "pid", lambda: processes(args, psutil),
                                   args.segundos, psutil))
    finally:
        for child in children:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        temp.cleanup()
    print(f"\n{'Proceso':34} {'n':>3} {'CPU media':>10} {'CPU máx':>9} {'RAM media':>11} {'RAM máx':>9}")
    for r in results:
        print(f"{r['proceso']:34} {r['procesos']:>3} {r['cpu_media']:>9.1f}% {r['cpu_max']:>8.1f}% "
              f"{r['ram_media']:>8.0f} MB {r['ram_max']:>6.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

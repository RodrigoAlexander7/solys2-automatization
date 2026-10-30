"""Línea de comandos: python -m smp22x <comando> ...

  ports                      lista los puertos COM (el PMU485 aparece como FTDI)
  scan                       busca sensores en un rango de direcciones
  info                       identifica los sensores y hace una lectura completa
  log                        adquisición continua a CSV (+ gráficas al terminar)
  plot <carpeta|csv>         genera las gráficas de una prueba guardada
"""

from __future__ import annotations

import argparse
import re
import sys
import math
from pathlib import Path

from . import registers as reg
from .sensor import LW_CHECK_TOLERANCE_WM2
from .config import environment, settings
from .result import StorageFailure, require
from .tracing import configure_logging
from .transport import (
    BusError,
    PortError,
    SerialBus,
    autodetect_port,
    available_ports,
    read_transaction_wire_time_s,
)

# Margen supuesto por transacción además del tiempo en el cable: latencia USB del
# FTDI (16 ms por defecto) + tiempo de respuesta del sensor.
OVERHEAD_PER_READ_S = 0.030


def parse_duration(text: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text.lower())
    if not match:
        raise argparse.ArgumentTypeError(f"duración no válida: {text!r} (ej. 90, 30s, 10m, 2h)")
    value, unit = float(match[1]), match[2] or "s"
    return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _add_bus_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", default="auto", help="puerto COM del PMU485 (auto por defecto)")
    p.add_argument("--baud", type=int, default=reg.DEFAULT_BAUDRATE, help="baudios (fábrica: 19200)")
    p.add_argument("--parity", default=reg.DEFAULT_PARITY, choices=["E", "N", "O"], help="paridad (fábrica: E)")
    p.add_argument("--timeout", type=float, default=0.3, help="timeout de respuesta en s (0.3)")
    p.add_argument("--simulate", action="store_true", help="usar sensores simulados (sin hardware)")


def _add_address_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--addresses", "-a", type=int, nargs="+", default=[2, 3, 4],
                   help="direcciones Modbus de los sensores (2 3 4)")
    p.add_argument("--pyrgeometers", type=int, nargs="*", default=[2],
                   help="cuáles de esas direcciones son pirgeómetros SGR4 (2); "
                        "sin valores = ninguno")


# La instalación real: pirgeómetro SGR4 en la dirección 2 y piranómetros SMP22x en 3 y 4
SIMULATED_ADDRESSES = [2, 3, 4]
SIMULATED_PYRGEOMETERS = [2]


def _open_bus(args, baud: int | None = None, parity: str | None = None):
    """Abre el bus con los parámetros de args, o con baud/parity si se indican
    (cmd_scan prueba varias combinaciones sin tocar args)."""
    baud = args.baud if baud is None else baud
    parity = args.parity if parity is None else parity
    if args.simulate:
        from .simulator import SimulatedBus
        return SimulatedBus(SIMULATED_ADDRESSES, baudrate=baud, parity=parity,
                            timeout=args.timeout, pyrgeometers=SIMULATED_PYRGEOMETERS)
    port = autodetect_port() if args.port == "auto" else args.port
    return SerialBus(port, baudrate=baud, parity=parity, timeout=args.timeout)


def sensor_types(args) -> dict[int, str]:
    """Tipo de cada dirección según --addresses y --pyrgeometers."""
    unknown = sorted(set(args.pyrgeometers) - set(args.addresses))
    if unknown:
        raise SystemExit(f"--pyrgeometers {unknown} no está en --addresses {args.addresses}")
    return {
        a: reg.PYRGEOMETER if a in args.pyrgeometers else reg.PYRANOMETER for a in args.addresses
    }


def min_cycle_s(block_counts: list[int], baudrate: int, parity: str = reg.DEFAULT_PARITY) -> float:
    """Duración mínima de un ciclo: una lectura por sensor, cada una con su bloque."""
    return sum(
        read_transaction_wire_time_s(n, baudrate, parity) + OVERHEAD_PER_READ_S for n in block_counts
    )


def check_interval(
    interval: float, block_counts: list[int], baudrate: int, parity: str = reg.DEFAULT_PARITY
) -> None:
    floor = min_cycle_s(block_counts, baudrate, parity)
    if interval < floor:
        raise SystemExit(
            f"Intervalo {interval:g} s demasiado corto: leer {len(block_counts)} sensores a "
            f"{baudrate} baudios necesita ~{floor * 1000:.0f} ms por ciclo."
        )
    if interval < 1.0:
        print(
            "aviso: los registros del SMP se actualizan 1 vez por segundo; con intervalos < 1 s "
            "se leerán valores repetidos y se ocupa el bus sin ganar información."
        )
    elif interval > 3.0:
        print("aviso: IEC 61724-1 Clase A pide muestrear la irradiancia cada 3 s o menos.")


# ---------------------------------------------------------------- comandos
def cmd_ports(args) -> int:
    ports = available_ports()
    if not ports:
        print("No hay puertos COM. ¿Está conectado el PMU485? (Administrador de dispositivos > Puertos)")
        return 1
    for p in ports:
        mark = "  <- FTDI (probable PMU485)" if p.is_ftdi else ""
        print(f"{p.device:8} {p.description}{mark}")
    return 0


def cmd_scan(args) -> int:
    from .sensor import SMPSensor

    addresses = list(range(args.first, args.last + 1))
    combos = [(args.baud, args.parity)]
    if args.all_baud:
        combos = [(b, p) for b in (19200, 9600, 38400, 57600, 115200) for p in ("E", "N", "O")]
    found = 0
    for baud, parity in combos:
        bus = _open_bus(args, baud=baud, parity=parity)
        print(f"Buscando en {bus.description}, direcciones {args.first}-{args.last}...")
        try:
            for address in addresses:
                try:
                    info = SMPSensor(bus, address).read_info()
                except BusError:
                    continue
                found += 1
                kind = reg.DEVICE_SENSOR_TYPES.get(info.device_type)
                kind_label = f", {reg.SENSOR_LABELS[kind]}" if kind else ""
                print(f"  #{address}: {info.model}{kind_label} serie {info.serial_label} "
                      f"(IO_DEVICE_TYPE {info.device_type}, data model {info.datamodel_version})")
        finally:
            bus.close()
    print(f"{found} sensor(es) encontrado(s).")
    return 0 if found else 1


def _print_pyrgeometer_check(sample) -> bool:
    """Imprime Ld↓ leído frente a neta + σT⁴. Devuelve False si no cuadran."""
    print(f"  radiación neta {sample.irradiance} W/m² (bruta {sample.raw_irradiance}), "
          f"desv. {sample.stdev} W/m²")
    print(f"  onda larga descendente Ld↓ {sample.lw_down} W/m² (bruta {sample.raw_lw_down})")
    calc = sample.lw_down_calc
    if calc is None or sample.lw_down is None:
        return True
    diff = sample.lw_down - calc
    print(f"  verificación: neta + σT⁴ = {calc:.1f} W/m², diferencia con Ld↓ {diff:+.1f} W/m²")
    if abs(diff) > LW_CHECK_TOLERANCE_WM2:
        print(f"  aviso: la diferencia supera {LW_CHECK_TOLERANCE_WM2:g} W/m². Este firmware "
              "podría no seguir el mapa de registros 10–11 del manual V20.")
        return False
    return True


def cmd_info(args) -> int:
    from .sensor import SMPSensor

    types = sensor_types(args)
    bus = _open_bus(args)
    print(f"Bus: {bus.description}")
    failures = 0
    try:
        for address in args.addresses:
            sensor = SMPSensor(bus, address, types[address])
            try:
                info = sensor.read_info()
            except BusError as exc:
                print(f"\n#{address}: sin respuesta ({exc})")
                failures += 1
                continue
            sample = sensor.read_sample(with_diagnostics=True)
            print(f"\n#{address}: {info.model} ({reg.SENSOR_LABELS[sensor.sensor_type]})  "
                  f"serie {info.serial_label}")
            print(f"  data model {info.datamodel_version}, software {info.software_version}, "
                  f"hardware {info.hardware_version}, IO_NODE_ID {info.node_id}")
            for w in info.warnings:
                print(f"  aviso: {w}")
            if not sample.ok:
                print(f"  lectura fallida: {sample.error}")
                failures += 1
                continue
            mode = reg.OPERATIONAL_MODES.get(sample.mode, sample.mode)
            print(f"  modo {mode}, status 0x{sample.status:04X} {sample.status_names or ''}, "
                  f"escala {sample.scale_factor}")
            if sensor.sensor_type == reg.PYRGEOMETER:
                if not _print_pyrgeometer_check(sample):
                    failures += 1
            else:
                print(f"  irradiancia {sample.irradiance} W/m² (bruta {sample.raw_irradiance}), "
                      f"desv. {sample.stdev} W/m²")
            print(f"  T cuerpo {sample.body_temp_c} °C, alimentación {sample.supply_v} V, "
                  f"HR interna {sample.rh_pct} %, presión interna {sample.pressure_hpa} hPa")
            print(f"  tiempo de respuesta {sample.rtt_ms:.1f} ms")
            if args.clear and sample.status & reg.CLEARABLE_MASK:
                sensor.clear_error()
                print("  IO_CLEAR_ERROR enviado (flags de arranque borrados)")
    finally:
        bus.close()
    return 1 if failures else 0


def cmd_log(args) -> int:
    from .acquisition import Acquisition, AcquisitionConfig

    types = sensor_types(args)
    if not math.isfinite(args.interval) or args.interval <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("Intervalo y timeout deben ser positivos y finitos")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        raise ValueError("Duración debe ser positiva y finita")
    if not math.isfinite(args.diag_every) or args.diag_every <= 0:
        raise ValueError("diag-every debe ser positivo y finito")
    blocks = [
        reg.PYRGEOMETER_BLOCK_COUNT if t == reg.PYRGEOMETER else reg.FAST_BLOCK_COUNT
        for t in types.values()
    ]
    check_interval(args.interval, blocks, args.baud, args.parity)
    bus = _open_bus(args)
    cfg = AcquisitionConfig(
        addresses=args.addresses,
        pyrgeometers=[a for a, t in types.items() if t == reg.PYRGEOMETER],
        interval_s=args.interval,
        duration_s=args.duration,
        diag_every_s=args.diag_every,
        clear_errors=not args.no_clear,
        out_dir=Path(args.out),
        label=args.label,
        simulated=args.simulate,
        live=not args.quiet,
        db_path=Path(args.db),
    )
    try:
        run_dir = Acquisition(bus, cfg).run()
    finally:
        bus.close()
    if not args.no_plot:
        return _plot(run_dir)
    return 0


def _plot(path: Path) -> int:
    from .plotting import plot_run, summarize, load_run

    outputs = plot_run(path)
    _, raw, _, _ = load_run(path)
    print("\n" + summarize(raw).to_string(index=False))
    print("\nGráficas:")
    for out in outputs:
        print(f"  {out}")
    return 0


def cmd_plot(args) -> int:
    return _plot(Path(args.path))


def cmd_dashboard(args) -> int:
    import uvicorn
    from dataclasses import replace
    from .api import create_app
    cfg = replace(settings(), db=Path(args.db))
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, workers=1)
    return 0


def cmd_export(args) -> int:
    from .storage import Reader
    require(Reader(Path(args.db)).export(args.run_id, Path(args.out)))
    return 0


def cmd_backup(args) -> int:
    from .storage import Reader
    require(Reader(Path(args.db)).backup(Path(args.destination)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m smp22x", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ports", help="lista puertos COM")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("scan", help="busca sensores en el bus")
    _add_bus_args(p)
    p.add_argument("--first", type=int, default=1, help="primera dirección (1)")
    p.add_argument("--last", type=int, default=20, help="última dirección (20)")
    p.add_argument("--all-baud", action="store_true",
                   help="probar también 9600/38400/57600/115200 y paridades N/O")
    p.set_defaults(func=cmd_scan, timeout=0.15)

    p = sub.add_parser("info", help="identifica los sensores y hace una lectura")
    _add_bus_args(p)
    _add_address_arg(p)
    p.add_argument("--clear", action="store_true", help="enviar IO_CLEAR_ERROR si hay flags de arranque")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("log", help="adquisición continua a CSV")
    _add_bus_args(p)
    _add_address_arg(p)
    p.add_argument("--interval", "-i", type=float, default=1.0, help="segundos entre ciclos (1)")
    p.add_argument("--duration", "-d", type=parse_duration, default=None,
                   help="duración: 600, 30s, 10m, 2h (sin límite si se omite; Ctrl+C para parar)")
    p.add_argument("--diag-every", type=float, default=60.0,
                   help="segundos entre lecturas de HR/presión interna (60)")
    p.add_argument("--out", default="data", help="carpeta de salida (data)")
    p.add_argument("--label", default="", help="etiqueta para el nombre de la carpeta")
    p.add_argument("--no-clear", action="store_true", help="no enviar IO_CLEAR_ERROR")
    p.add_argument("--no-plot", action="store_true", help="no generar gráficas al terminar")
    p.add_argument("--quiet", "-q", action="store_true", help="no imprimir cada ciclo")
    p.add_argument("--db", default="data/acquisition.sqlite3", help="SQLite compartido con dashboard")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("plot", help="grafica una prueba guardada")
    p.add_argument("path", help="carpeta de la prueba o su raw_1s.csv")
    p.set_defaults(func=cmd_plot)

    p = sub.add_parser("dashboard", help="API y dashboard; no consulta Modbus")
    p.add_argument("--db", default="data/acquisition.sqlite3")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("export", help="exporta una corrida SQLite a CSV compatible")
    p.add_argument("run_id")
    p.add_argument("--out", required=True, help="carpeta nueva")
    p.add_argument("--db", default="data/acquisition.sqlite3")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("backup", help="copia consistente de SQLite en uso")
    p.add_argument("destination")
    p.add_argument("--db", default="data/acquisition.sqlite3")
    p.set_defaults(func=cmd_backup)

    e = environment()
    for name in ("scan", "info", "log"):
        sub.choices[name].set_defaults(port=e.get("SMP_SERIAL_PORT", "auto"),
            baud=int(e.get("SMP_BAUD", "19200")), parity=e.get("SMP_PARITY", "E"),
            timeout=float(e.get("SMP_TIMEOUT_S", "0.3")), simulate=e.get("SMP_SIMULATE", "0").lower() in {"1", "true", "yes"})
    for name in ("info", "log"):
        sub.choices[name].set_defaults(addresses=[int(x) for x in e.get("SMP_ADDRESSES", "2 3 4").split()],
            pyrgeometers=[int(x) for x in e.get("SMP_PYRGEOMETERS", "2").split()])
    sub.choices["log"].set_defaults(interval=float(e.get("SMP_INTERVAL_S", "1")),
        out=e.get("SMP_OUT_DIR", "data"), label=e.get("SMP_LABEL", ""))
    for name in ("log", "dashboard", "export", "backup"):
        sub.choices[name].set_defaults(db=e.get("SMP_DB_PATH", "data/acquisition.sqlite3"))
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        configure_logging(environment().get("SMP_LOG_LEVEL", "INFO"))
        args = build_parser().parse_args(argv)
        if args.command == "log":
            import signal
            def stop(signum, frame):
                raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM, stop)
        return args.func(args)
    except PortError as exc:
        print(f"Error de puerto: {exc}", file=sys.stderr)
        return 2
    except (ValueError, StorageFailure) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3

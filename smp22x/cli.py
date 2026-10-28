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
from pathlib import Path

from . import registers as reg
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


SIMULATED_ADDRESSES = [2, 3, 4]  # la instalación real: tres SMP22x en 2, 3 y 4


def _open_bus(args):
    if args.simulate:
        from .simulator import SimulatedBus
        return SimulatedBus(SIMULATED_ADDRESSES, baudrate=args.baud, timeout=args.timeout)
    port = autodetect_port() if args.port == "auto" else args.port
    return SerialBus(port, baudrate=args.baud, parity=args.parity, timeout=args.timeout)


def min_cycle_s(n_sensors: int, baudrate: int) -> float:
    per_read = read_transaction_wire_time_s(reg.FAST_BLOCK_COUNT, baudrate) + OVERHEAD_PER_READ_S
    return n_sensors * per_read


def check_interval(interval: float, n_sensors: int, baudrate: int) -> None:
    floor = min_cycle_s(n_sensors, baudrate)
    if interval < floor:
        raise SystemExit(
            f"Intervalo {interval:g} s demasiado corto: leer {n_sensors} sensores a {baudrate} "
            f"baudios necesita ~{floor * 1000:.0f} ms por ciclo."
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
        args.baud, args.parity = baud, parity
        bus = _open_bus(args)
        print(f"Buscando en {bus.description}, direcciones {args.first}-{args.last}...")
        try:
            for address in addresses:
                try:
                    info = SMPSensor(bus, address).read_info()
                except BusError:
                    continue
                found += 1
                print(f"  #{address}: {info.model} serie {info.serial_label} "
                      f"(data model {info.datamodel_version})")
        finally:
            bus.close()
    print(f"{found} sensor(es) encontrado(s).")
    return 0 if found else 1


def cmd_info(args) -> int:
    from .sensor import SMPSensor

    bus = _open_bus(args)
    print(f"Bus: {bus.description}")
    failures = 0
    try:
        for address in args.addresses:
            sensor = SMPSensor(bus, address)
            try:
                info = sensor.read_info()
            except BusError as exc:
                print(f"\n#{address}: sin respuesta ({exc})")
                failures += 1
                continue
            sample = sensor.read_sample(with_diagnostics=True)
            print(f"\n#{address}: {info.model}  serie {info.serial_label}")
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

    check_interval(args.interval, len(args.addresses), args.baud)
    bus = _open_bus(args)
    cfg = AcquisitionConfig(
        addresses=args.addresses,
        interval_s=args.interval,
        duration_s=args.duration,
        diag_every_s=args.diag_every,
        clear_errors=not args.no_clear,
        out_dir=Path(args.out),
        label=args.label,
        simulated=args.simulate,
        live=not args.quiet,
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
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("plot", help="grafica una prueba guardada")
    p.add_argument("path", help="carpeta de la prueba o su raw_1s.csv")
    p.set_defaults(func=cmd_plot)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PortError as exc:
        print(f"Error de puerto: {exc}", file=sys.stderr)
        return 2

"""Gráficas y resumen estadístico de una prueba guardada por acquisition.py."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402


def _ui_font() -> str:
    try:
        font_manager.findfont("Segoe UI", fallback_to_default=False)
        return "Segoe UI"
    except ValueError:
        return "DejaVu Sans"



# Colores fijos por sensor (el color sigue a la dirección, no al orden en el archivo)
SENSOR_COLORS = {2: "#2a78d6", 3: "#eb6834", 4: "#1baf7a"}
EXTRA_COLORS = ["#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CRITICAL = "#d03b3b"

plt.rcParams.update({
    "font.family": _ui_font(),
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_2,
    "axes.titlecolor": INK,
    "axes.titlesize": 11,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelcolor": INK_2,
    "ytick.labelcolor": INK_2,
    "legend.frameon": False,
    "legend.labelcolor": INK_2,
    "savefig.facecolor": SURFACE,
})


def sensor_color(address: int, all_addresses: list[int]) -> str:
    if address in SENSOR_COLORS:
        return SENSOR_COLORS[address]
    extras = [a for a in sorted(all_addresses) if a not in SENSOR_COLORS]
    return EXTRA_COLORS[extras.index(address) % len(EXTRA_COLORS)]


def load_run(path: Path) -> tuple[Path, pd.DataFrame, pd.DataFrame | None, dict]:
    run_dir = path if path.is_dir() else path.parent
    raw = pd.read_csv(run_dir / "raw_1s.csv")
    # Hora local sin zona para el eje X (la zona queda en el CSV)
    raw["t"] = pd.to_datetime(raw["time_local"].str.slice(0, 23))
    minute_path = run_dir / "minute_avg.csv"
    minute = pd.read_csv(minute_path) if minute_path.exists() else None
    if minute is not None and not minute.empty:
        minute["t"] = pd.to_datetime(minute["minute_local"].str.slice(0, 19))
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return run_dir, raw, minute, meta


def _deviation_from_mean(raw: pd.DataFrame) -> pd.DataFrame:
    """Diferencia de cada sensor respecto a la media de todos en el mismo ciclo.

    Solo usa ciclos en los que todos los sensores dieron dato válido, para que
    la media sea comparable.
    """
    valid = raw[raw["valid"] == 1]
    wide = valid.pivot_table(index="cycle", columns="address", values="irradiance_wm2")
    wide = wide.dropna()
    if wide.shape[1] < 2 or wide.empty:
        return pd.DataFrame()
    dev = wide.sub(wide.mean(axis=1), axis=0)
    times = valid.groupby("cycle")["t"].first()
    dev.index = times.loc[dev.index]
    return dev


def _end_labels(fig, ax, items: list[tuple], min_gap_pt: float = 13.0) -> None:
    """Etiquetas directas al final de cada línea, separadas verticalmente para
    que no se solapen cuando los sensores miden casi lo mismo.

    items: (x, y, texto, color). Se llama después de fijar los límites.
    """
    if not items:
        return
    fig.canvas.draw()
    px_per_pt = fig.dpi / 72.0
    placed = []
    for x, y, text, color in sorted(items, key=lambda it: it[1]):
        y_px = ax.transData.transform((mdates.date2num(x), y))[1]
        target = y_px if not placed else max(y_px, placed[-1][1] + min_gap_pt * px_per_pt)
        placed.append(((x, y, text, color), target, y_px))
    for (x, y, text, color), target, y_px in placed:
        ann = ax.annotate(
            text, xy=(x, y), xytext=(6, (target - y_px) / px_per_pt),
            textcoords="offset points", va="center", fontsize=9, color=INK_2,
            bbox={"boxstyle": "round,pad=0.15", "fc": SURFACE, "ec": color, "lw": 1},
        )
        ann.set_in_layout(False)


def _headroom(ax, frac: float) -> None:
    """Deja una franja libre arriba para la leyenda."""
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi + (hi - lo) * frac)


def _format_time_axis(ax) -> None:
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def summarize(raw: pd.DataFrame) -> pd.DataFrame:
    rows = []
    dev = _deviation_from_mean(raw)
    for address, g in raw.groupby("address"):
        v = g[g["valid"] == 1]
        comm_err = int((g["rtt_ms"].isna() & g["error"].notna()).sum())
        # Lecturas idénticas a la anterior: si se sondea más rápido de lo que el
        # sensor actualiza el registro (1 vez/s), este porcentaje sube.
        irr = v["irradiance_wm2"]
        repeated = round(100 * (irr.diff() == 0).sum() / max(len(irr) - 1, 1), 1)
        rows.append({
            "sensor": int(address),
            "muestras": len(g),
            "validas_%": round(100 * len(v) / len(g), 1) if len(g) else 0.0,
            "errores_com": comm_err,
            "media_Wm2": round(v["irradiance_wm2"].mean(), 1),
            "min_Wm2": round(v["irradiance_wm2"].min(), 1),
            "max_Wm2": round(v["irradiance_wm2"].max(), 1),
            "desv_vs_media_Wm2": round(dev[address].mean(), 2) if address in dev else None,
            "T_cuerpo_C": round(v["body_temp_c"].mean(), 1),
            "V_alim": round(v["supply_v"].mean(), 1),
            "RTT_ms": round(g["rtt_ms"].mean(), 1),
            "repetidos_%": repeated,
        })
    return pd.DataFrame(rows)


def plot_run(path: Path) -> list[Path]:
    run_dir, raw, minute, meta = load_run(path)
    if raw.empty:
        raise ValueError(f"{run_dir / 'raw_1s.csv'} no tiene datos")
    addresses = sorted(int(a) for a in raw["address"].unique())
    simulated = bool(meta.get("simulated")) or run_dir.name.endswith("_SIM")
    tag = "  [DATOS SIMULADOS]" if simulated else ""
    day = raw["t"].iloc[0].strftime("%Y-%m-%d")
    outputs: list[Path] = []

    # ------------------------------------------------ figura 1: medición
    fig, (ax_irr, ax_dev, ax_temp) = plt.subplots(
        3, 1, figsize=(12, 9), sharex=True, height_ratios=[3, 1.6, 1.3],
        layout="constrained",
    )
    fig.suptitle(
        f"Piranómetros SMP22x — prueba {day}{tag}", x=0.01, ha="left",
        fontsize=13, fontweight="semibold", color=INK,
    )
    dev = _deviation_from_mean(raw)
    end_labels = []
    for address in addresses:
        color = sensor_color(address, addresses)
        g = raw[raw["address"] == address]
        v = g[g["valid"] == 1]
        label = f"Sensor #{address}"
        ax_irr.plot(v["t"], v["irradiance_wm2"], color=color, lw=1.4, label=label)
        bad = g[g["valid"] != 1]
        if not bad.empty:
            # Marcas en el borde inferior del panel (x en datos, y en fracción del eje)
            ax_irr.plot(
                bad["t"], [0.03] * len(bad), "|", color=CRITICAL, ms=8, mew=1.5,
                transform=ax_irr.get_xaxis_transform(),
            )
        if not v.empty:
            end_labels.append((v["t"].iloc[-1], v["irradiance_wm2"].iloc[-1], f"#{address}", color))
        if address in dev:
            ax_dev.plot(dev.index, dev[address], color=color, lw=1.2, label=label)
        ax_temp.plot(v["t"], v["body_temp_c"], color=color, lw=1.4, label=label)

    if minute is not None and not minute.empty and minute["complete"].sum() >= 2:
        full = minute[minute["complete"] == 1]
        for address in addresses:
            m = full[full["address"] == address]
            ax_irr.plot(
                m["t"] + pd.Timedelta(seconds=30), m["mean_wm2"], "o",
                color=sensor_color(address, addresses), ms=6, mec=SURFACE, mew=2,
            )
        ax_irr.plot([], [], "o", color=MUTED, ms=6, mec=SURFACE, mew=2, label="Promedio 1 min")

    if (raw["valid"] != 1).any():
        ax_irr.plot([], [], "|", color=CRITICAL, ms=8, mew=1.5, label="Muestra inválida / sin respuesta")
    ax_irr.set_title("Irradiancia global (IO_SENSOR1_DATA), muestreo 1 s")
    ax_irr.set_ylabel("W/m²")
    ax_irr.margins(x=0.03)
    _headroom(ax_irr, 0.12)
    ax_irr.legend(loc="upper left", ncols=5, fontsize=9)
    ax_dev.axhline(0, color=AXIS, lw=1)
    ax_dev.set_title("Diferencia de cada sensor respecto a la media de los tres")
    ax_dev.set_ylabel("W/m²")
    ax_temp.set_title("Temperatura interna del cuerpo")
    ax_temp.set_ylabel("°C")
    _format_time_axis(ax_temp)
    _end_labels(fig, ax_irr, end_labels)
    out = run_dir / "irradiancia.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    outputs.append(out)

    # ------------------------------------------------ figura 2: comunicación
    fig, (ax_rtt, ax_v) = plt.subplots(
        2, 1, figsize=(12, 6), sharex=True, height_ratios=[2, 1], layout="constrained",
    )
    fig.suptitle(
        f"Diagnóstico del bus RS-485 — {meta.get('bus', '')}{tag}", x=0.01, ha="left",
        fontsize=13, fontweight="semibold", color=INK,
    )
    for address in addresses:
        color = sensor_color(address, addresses)
        g = raw[raw["address"] == address]
        ok = g[g["rtt_ms"].notna()]
        ax_rtt.plot(ok["t"], ok["rtt_ms"], ".", color=color, ms=4, label=f"Sensor #{address}")
        fail = g[g["rtt_ms"].isna()]
        if not fail.empty:
            ax_rtt.plot(
                fail["t"], [0.05] * len(fail), "x", color=CRITICAL, ms=7, mew=1.5,
                transform=ax_rtt.get_xaxis_transform(),
            )
        ax_v.plot(ok["t"], ok["supply_v"], color=color, lw=1.4, label=f"Sensor #{address}")
    if raw["rtt_ms"].isna().any():
        ax_rtt.plot([], [], "x", color=CRITICAL, ms=7, mew=1.5, label="Sin respuesta (timeout)")
    timing = meta.get("bus_timing", {})
    subtitle = "Tiempo de ida y vuelta por lectura (petición + respuesta)"
    if timing.get("cycle_ms_mean") is not None:
        subtitle += (
            f" · ciclo medio {timing['cycle_ms_mean']} ms"
            f" · ocupación ~{timing['bus_utilization_pct']} %"
        )
    ax_rtt.set_title(subtitle)
    ax_rtt.set_ylabel("ms")
    ax_rtt.set_ylim(bottom=0)
    _headroom(ax_rtt, 0.15)
    ax_rtt.legend(loc="upper left", ncols=4, fontsize=9)
    ax_v.set_title("Tensión de alimentación medida por el sensor")
    ax_v.set_ylabel("V")
    _format_time_axis(ax_v)
    out = run_dir / "comunicacion.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    outputs.append(out)

    summary = summarize(raw)
    summary.to_csv(run_dir / "resumen.csv", index=False)
    outputs.append(run_dir / "resumen.csv")
    return outputs

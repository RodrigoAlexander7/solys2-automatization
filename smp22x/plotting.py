"""Gráficas y resumen estadístico de una prueba guardada por acquisition.py.

Piranómetros y pirgeómetro miden magnitudes distintas (onda corta, ~0–1200 W/m²,
y onda larga, ~250–450 W/m²), así que van en figuras separadas:
irradiancia.png para los piranómetros y onda_larga.png para el pirgeómetro.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from . import registers as reg  # noqa: E402


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


def _blackbody_wm2(temp_c: pd.Series) -> pd.Series:
    """σ·T⁴ del detector (manual SGR4 §7.1.5)."""
    return reg.STEFAN_BOLTZMANN * (temp_c + 273.15) ** 4


def load_run(path: Path) -> tuple[Path, pd.DataFrame, pd.DataFrame | None, dict]:
    run_dir = path if path.is_dir() else path.parent
    raw = pd.read_csv(run_dir / "raw_1s.csv")
    # Hora local sin zona para el eje X (la zona queda en el CSV)
    raw["t"] = pd.to_datetime(raw["time_local"].str.slice(0, 23))
    # Las pruebas anteriores al soporte del pirgeómetro no tienen estas columnas
    # y se leían todos los sensores como piranómetros.
    if "sensor_type" not in raw:
        raw["sensor_type"] = reg.PYRANOMETER
    for col in ("lw_down_wm2", "raw_lw_down_wm2"):
        if col not in raw:
            raw[col] = float("nan")
    # Magnitud principal de cada fila: irradiancia global o Ld↓
    is_pyrg = raw["sensor_type"] == reg.PYRGEOMETER
    raw["measurement_wm2"] = raw["lw_down_wm2"].where(is_pyrg, raw["irradiance_wm2"])
    minute_path = run_dir / "minute_avg.csv"
    minute = pd.read_csv(minute_path) if minute_path.exists() else None
    if minute is not None and not minute.empty:
        minute["t"] = pd.to_datetime(minute["minute_local"].str.slice(0, 19))
        if "sensor_type" not in minute:
            minute["sensor_type"] = reg.PYRANOMETER
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return run_dir, raw, minute, meta


def _sensor_types(raw: pd.DataFrame) -> dict[int, str]:
    return {int(a): t for a, t in raw.groupby("address")["sensor_type"].first().items()}


def _deviation_from_mean(raw: pd.DataFrame) -> pd.DataFrame:
    """Diferencia de cada piranómetro respecto a la media de los piranómetros en
    el mismo ciclo. El pirgeómetro mide otra magnitud y no entra en la media.

    Solo usa ciclos en los que todos dieron dato válido, para que la media sea
    comparable.
    """
    valid = raw[(raw["valid"] == 1) & (raw["sensor_type"] == reg.PYRANOMETER)]
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


def _sensor_label(address: int, sensor_type: str) -> str:
    return f"Sensor #{address} (SGR4)" if sensor_type == reg.PYRGEOMETER else f"Sensor #{address}"


def _mark_invalid(ax, rows: pd.DataFrame) -> None:
    """Marcas en el borde inferior del panel (x en datos, y en fracción del eje)."""
    if not rows.empty:
        ax.plot(
            rows["t"], [0.03] * len(rows), "|", color=CRITICAL, ms=8, mew=1.5,
            transform=ax.get_xaxis_transform(),
        )


def _plot_minute_means(ax, minute: pd.DataFrame | None, addresses: list[int], all_addresses) -> None:
    if minute is None or minute.empty or minute["complete"].sum() < 2:
        return
    full = minute[(minute["complete"] == 1) & minute["address"].isin(addresses)]
    if full.empty:
        return
    for address in addresses:
        m = full[full["address"] == address]
        ax.plot(
            m["t"] + pd.Timedelta(seconds=30), m["mean_wm2"], "o",
            color=sensor_color(address, all_addresses), ms=6, mec=SURFACE, mew=2,
        )
    ax.plot([], [], "o", color=MUTED, ms=6, mec=SURFACE, mew=2, label="Promedio 1 min")


def summarize(raw: pd.DataFrame) -> pd.DataFrame:
    rows = []
    dev = _deviation_from_mean(raw)
    for address, g in raw.groupby("address"):
        v = g[g["valid"] == 1]
        kind = g["sensor_type"].iloc[0]
        is_pyrg = kind == reg.PYRGEOMETER
        comm_err = int((g["rtt_ms"].isna() & g["error"].notna()).sum())
        # Lecturas idénticas a la anterior: si se sondea más rápido de lo que el
        # sensor actualiza el registro (1 vez/s), este porcentaje sube.
        val = v["measurement_wm2"]
        repeated = round(100 * (val.diff() == 0).sum() / max(len(val) - 1, 1), 1)
        # Pirgeómetro: Ld↓ leído menos neta + σT⁴. Cerca de 0 confirma que los
        # registros 10–11 son Ld↓ en este firmware.
        ld_check = v["lw_down_wm2"] - (v["irradiance_wm2"] + _blackbody_wm2(v["body_temp_c"]))
        rows.append({
            "sensor": int(address),
            "tipo": reg.SENSOR_LABELS.get(kind, kind),
            "muestras": len(g),
            "validas_%": round(100 * len(v) / len(g), 1) if len(g) else 0.0,
            "errores_com": comm_err,
            "media_Wm2": round(val.mean(), 1),
            "min_Wm2": round(val.min(), 1),
            "max_Wm2": round(val.max(), 1),
            "desv_vs_media_Wm2": round(dev[address].mean(), 2) if address in dev else None,
            "neta_media_Wm2": round(v["irradiance_wm2"].mean(), 1) if is_pyrg else None,
            "Ld_menos_calc_Wm2": round(ld_check.mean(), 2) if is_pyrg else None,
            "T_cuerpo_C": round(v["body_temp_c"].mean(), 1),
            "V_alim": round(v["supply_v"].mean(), 1),
            "RTT_ms": round(g["rtt_ms"].mean(), 1),
            "repetidos_%": repeated,
        })
    return pd.DataFrame(rows)


def _plot_shortwave(run_dir, raw, minute, addresses, all_addresses, day, tag) -> Path:
    """irradiancia.png: piranómetros (onda corta)."""
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
        color = sensor_color(address, all_addresses)
        g = raw[raw["address"] == address]
        v = g[g["valid"] == 1]
        label = f"Sensor #{address}"
        ax_irr.plot(v["t"], v["irradiance_wm2"], color=color, lw=1.4, label=label)
        _mark_invalid(ax_irr, g[g["valid"] != 1])
        if not v.empty:
            end_labels.append((v["t"].iloc[-1], v["irradiance_wm2"].iloc[-1], f"#{address}", color))
        if address in dev:
            ax_dev.plot(dev.index, dev[address], color=color, lw=1.2, label=label)
        ax_temp.plot(v["t"], v["body_temp_c"], color=color, lw=1.4, label=label)

    _plot_minute_means(ax_irr, minute, addresses, all_addresses)
    if (raw[raw["address"].isin(addresses)]["valid"] != 1).any():
        ax_irr.plot([], [], "|", color=CRITICAL, ms=8, mew=1.5, label="Muestra inválida / sin respuesta")
    ax_irr.set_title("Irradiancia global (IO_SENSOR1_DATA), muestreo 1 s")
    ax_irr.set_ylabel("W/m²")
    ax_irr.margins(x=0.03)
    _headroom(ax_irr, 0.12)
    ax_irr.legend(loc="upper left", ncols=5, fontsize=9)
    ax_dev.axhline(0, color=AXIS, lw=1)
    ax_dev.set_title(f"Diferencia de cada piranómetro respecto a la media de los {len(addresses)}")
    ax_dev.set_ylabel("W/m²")
    ax_temp.set_title("Temperatura interna del cuerpo")
    ax_temp.set_ylabel("°C")
    _format_time_axis(ax_temp)
    _end_labels(fig, ax_irr, end_labels)
    out = run_dir / "irradiancia.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def _plot_longwave(run_dir, raw, minute, addresses, all_addresses, day, tag) -> Path:
    """onda_larga.png: pirgeómetro (onda larga). Ld↓ es lo que interesa; la neta
    y la verificación neta + σT⁴ sirven para comprobar la lectura."""
    fig, (ax_ld, ax_net, ax_temp) = plt.subplots(
        3, 1, figsize=(12, 9), sharex=True, height_ratios=[3, 1.6, 1.3],
        layout="constrained",
    )
    fig.suptitle(
        f"Pirgeómetro SGR4 — prueba {day}{tag}", x=0.01, ha="left",
        fontsize=13, fontweight="semibold", color=INK,
    )
    for address in addresses:
        color = sensor_color(address, all_addresses)
        g = raw[raw["address"] == address]
        v = g[g["valid"] == 1]
        label = f"Sensor #{address}"
        ax_ld.plot(v["t"], v["lw_down_wm2"], color=color, lw=1.4, label=f"{label}: Ld↓ (IO_SENSOR2_DATA)")
        calc = v["irradiance_wm2"] + _blackbody_wm2(v["body_temp_c"])
        ax_ld.plot(v["t"], calc, color=INK_2, lw=0.9, ls="--", label=f"{label}: neta + σT⁴ (verificación)")
        _mark_invalid(ax_ld, g[g["valid"] != 1])
        ax_net.plot(v["t"], v["irradiance_wm2"], color=color, lw=1.2, label=label)
        ax_temp.plot(v["t"], v["body_temp_c"], color=color, lw=1.4, label=label)

    _plot_minute_means(ax_ld, minute, addresses, all_addresses)
    if (raw[raw["address"].isin(addresses)]["valid"] != 1).any():
        ax_ld.plot([], [], "|", color=CRITICAL, ms=8, mew=1.5, label="Muestra inválida / sin respuesta")
    ax_ld.set_title("Radiación de onda larga descendente Ld↓, muestreo 1 s")
    ax_ld.set_ylabel("W/m²")
    ax_ld.margins(x=0.03)
    _headroom(ax_ld, 0.15)
    ax_ld.legend(loc="upper left", ncols=3, fontsize=9)
    ax_net.axhline(0, color=AXIS, lw=1)
    ax_net.set_title("Radiación neta del detector (IO_SENSOR1_DATA): negativa si el cielo está más frío que el sensor")
    ax_net.set_ylabel("W/m²")
    ax_temp.set_title("Temperatura interna del cuerpo (entra en σT⁴)")
    ax_temp.set_ylabel("°C")
    _format_time_axis(ax_temp)
    out = run_dir / "onda_larga.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def _plot_comm(run_dir, raw, meta, addresses, types, tag) -> Path:
    """comunicacion.png: tiempos de respuesta, timeouts y alimentación de todos los sensores."""
    fig, (ax_rtt, ax_v) = plt.subplots(
        2, 1, figsize=(12, 6), sharex=True, height_ratios=[2, 1], layout="constrained",
    )
    fig.suptitle(
        f"Diagnóstico del bus RS-485 — {meta.get('bus', '')}{tag}", x=0.01, ha="left",
        fontsize=13, fontweight="semibold", color=INK,
    )
    for address in addresses:
        color = sensor_color(address, addresses)
        label = _sensor_label(address, types[address])
        g = raw[raw["address"] == address]
        ok = g[g["rtt_ms"].notna()]
        ax_rtt.plot(ok["t"], ok["rtt_ms"], ".", color=color, ms=4, label=label)
        fail = g[g["rtt_ms"].isna()]
        if not fail.empty:
            ax_rtt.plot(
                fail["t"], [0.05] * len(fail), "x", color=CRITICAL, ms=7, mew=1.5,
                transform=ax_rtt.get_xaxis_transform(),
            )
        ax_v.plot(ok["t"], ok["supply_v"], color=color, lw=1.4, label=label)
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
    return out


def range_figure(rows: list[dict], resolution: str, logbook: list[dict], footer: str, simulated: bool,
                 start_ms: int | None = None, end_ms: int | None = None) -> bytes:
    """PNG a pedido para un intervalo (registros ya anotados). No se guarda en disco.

    Paneles: onda corta (GHI y DHI), onda larga (Ld↓ y neta + σT⁴) y, si hay, la DNI
    estimada. Cruces rojas: valores con alguna prueba de QC fallida. Líneas
    verticales: notas de la bitácora.
    """
    import io
    from datetime import datetime

    def local(t_ms: int) -> datetime:
        return datetime.fromtimestamp(t_ms / 1000)  # hora local del PC, con su horario de verano

    value_key = "mean_wm2" if resolution == "1min" else "measurement"
    time_key = "minute_ms" if resolution == "1min" else "t_ms"
    offset_ms = 30_000 if resolution == "1min" else 0
    frame = pd.DataFrame([{
        "t": local(r[time_key] + offset_ms),
        "address": r["address"], "label": r.get("label") or f"#{r['address']}", "role": r["role"],
        "value": r[value_key] if (r["valid"] if resolution != "1min" else r["n_valid"] > 0) else None,
        "calc": r.get("lw_down_calc") if resolution != "1min" else None,
        "dni": r.get("dni_est_wm2"), "fail": r["qc"]["summary"] in ("bad", "suspect"),
    } for r in rows]) if rows else pd.DataFrame(columns=["t", "address", "label", "role", "value", "calc", "dni", "fail"])
    shortwave = frame[frame["role"].isin(["GHI", "DHI", "SHADE_OFF"])]
    longwave = frame[frame["role"] == "LWD"]
    has_dni = frame["dni"].notna().any()
    panels = 1 + (not longwave.empty) + bool(has_dni)
    fig, axes = plt.subplots(panels, 1, figsize=(12, 3.4 * panels + 0.8), sharex=True, layout="constrained", squeeze=False)
    axes = list(axes[:, 0])
    tag = "  [DATOS SIMULADOS]" if simulated else ""
    fig.suptitle(f"Radiometría — {'promedios de 1 min' if resolution == '1min' else 'muestras de 1 s'}{tag}",
                 x=0.01, ha="left", fontsize=13, fontweight="bold", color=INK)
    addresses = sorted(int(a) for a in frame["address"].unique()) if not frame.empty else []
    marker = "." if len(frame) < 300 else None
    ax = axes[0]
    for (address, role), g in shortwave.groupby(["address", "role"]):
        name = {"GHI": "global", "DHI": "difusa", "SHADE_OFF": "sombra no garantizada"}.get(role, role)
        ax.plot(g["t"], g["value"], lw=1.2, marker=marker, color=sensor_color(int(address), addresses),
                label=f"{g['label'].iloc[0]} · {name}")
        bad = g[g["fail"] & g["value"].notna()]
        if not bad.empty:
            ax.plot(bad["t"], bad["value"], "x", color=CRITICAL, ms=4)
    ax.set_title("Onda corta (sin corrección adicional)")
    ax.set_ylabel("W/m²")
    panel = 1
    if not longwave.empty:
        ax2 = axes[panel]
        panel += 1
        for address, g in longwave.groupby("address"):
            color = sensor_color(int(address), addresses)
            ax2.plot(g["t"], g["value"], lw=1.2, marker=marker, color=color, label=f"{g['label'].iloc[0]} · Ld↓")
            if g["calc"].notna().any():
                ax2.plot(g["t"], g["calc"], lw=1, ls="--", color=MUTED, label="neta + σT⁴ (comprobación)")
        ax2.set_title("Onda larga descendente")
        ax2.set_ylabel("W/m²")
    if has_dni:
        ax3 = axes[panel]
        d = frame[frame["role"] == "DHI"]  # incluye NaN para cortar la línea fuera de condiciones
        ax3.plot(d["t"], pd.to_numeric(d["dni"]), lw=1.2, marker=marker, color="#7966a8",
                 label="DNI estimada (GHI − DHI)/cos z")
        ax3.set_title("DNI estimada — derivada, no medida")
        ax3.set_ylabel("W/m²")
    if (frame["fail"] & frame["value"].notna()).any():
        axes[0].plot([], [], "x", color=CRITICAL, label="QC con falla")
    notes: dict[int, list[str]] = {}
    for entry in logbook:
        if not entry.get("voided"):
            notes.setdefault(entry["t_ms"] // 60_000, []).append(entry["category_label"])
    for minute, labels in sorted(notes.items()):
        t = local(minute * 60_000)
        for a in axes:
            a.axvline(t, color=AXIS, lw=1, ls=":")
        axes[0].annotate(" · ".join(dict.fromkeys(labels)), xy=(t, 1), xycoords=("data", "axes fraction"),
                         fontsize=8, color=INK_2, rotation=90, va="top", ha="right")
    for a in axes:
        if a.get_legend_handles_labels()[0]:
            a.legend(loc="upper left", fontsize=8, ncols=3)
        _format_time_axis(a)
        if start_ms is not None and end_ms is not None:
            a.set_xlim(local(start_ms), local(end_ms))
    if frame.empty:
        axes[0].text(0.5, 0.5, "Sin datos en el intervalo", transform=axes[0].transAxes, ha="center", color=MUTED)
    fig.supxlabel(footer, fontsize=7.5, color=MUTED, x=0.01, ha="left")
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=120)
    plt.close(fig)
    return out.getvalue()


def plot_run(path: Path) -> list[Path]:
    run_dir, raw, minute, meta = load_run(path)
    if raw.empty:
        raise ValueError(f"{run_dir / 'raw_1s.csv'} no tiene datos")
    addresses = sorted(int(a) for a in raw["address"].unique())
    types = _sensor_types(raw)
    pyranometers = [a for a in addresses if types[a] == reg.PYRANOMETER]
    pyrgeometers = [a for a in addresses if types[a] == reg.PYRGEOMETER]
    simulated = bool(meta.get("simulated")) or run_dir.name.endswith("_SIM")
    tag = "  [DATOS SIMULADOS]" if simulated else ""
    day = raw["t"].iloc[0].strftime("%Y-%m-%d")
    outputs: list[Path] = []

    if pyranometers:
        outputs.append(_plot_shortwave(run_dir, raw, minute, pyranometers, addresses, day, tag))
    if pyrgeometers:
        outputs.append(_plot_longwave(run_dir, raw, minute, pyrgeometers, addresses, day, tag))
    outputs.append(_plot_comm(run_dir, raw, meta, addresses, types, tag))

    summary = summarize(raw)
    summary.to_csv(run_dir / "resumen.csv", index=False)
    outputs.append(run_dir / "resumen.csv")
    return outputs

"""Ventana liviana (R29): Tkinter, sin navegador ni servidor web.

Muestra el último minuto de onda corta (#3, #4) y onda larga (#2), una tabla con
la última lectura de cada sensor y un formulario para guardar CSV. Lee la base
de adquisición en modo solo lectura; no escribe nada en ella. No contempla el
SOLYS2 ni la bola de sombra: los piranómetros se presentan como tales.

Para gastar lo mínimo, el ciclo de cada segundo usa solo la biblioteca estándar
(sqlite3, tkinter): no importa pandas, numpy ni matplotlib, y pyarrow solo se
carga al exportar días que ya están en el archivo Parquet. Si la ventana está
minimizada no consulta ni dibuja; al restaurarla vuelve a leer el último minuto
desde la base, así que no se pierde nada: los datos los guarda la adquisición.
"""
from __future__ import annotations

import csv
import json
import math
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import registers as reg

WINDOW_MS = 60_000
REFRESH_MS = 1000
GAP_MS = 3000
STALE_S = 5.0
DAY_MS = 86_400_000
MAX_SPAN_MS = {"1s": 31 * DAY_MS, "1min": 366 * DAY_MS}
SCHEMA_VERSION = 2  # debe coincidir con storage.SCHEMA_VERSION (lo comprueba un test)
COLORS = {2: "#447ec4", 3: "#d67c2e", 4: "#1d8a76"}
PALETTE = ["#7966a8", "#c2577a", "#5d8a2f", "#b5892a", "#3b8fa3"]
LOCAL_FORMAT = "%Y-%m-%d %H:%M"

SELECT_SINCE = ("SELECT address, sensor_type, t_ms, ok, valid, irradiance, lw_down, body_temp_c, supply_v, "
                "rtt_ms, status, error FROM samples WHERE t_ms >= ? ORDER BY t_ms")


# ------------------------------------------------------------------ utilidades
def color_for(address: int, addresses: list[int]) -> str:
    if address in COLORS:
        return COLORS[address]
    extra = sorted(a for a in set(addresses) if a not in COLORS)
    return PALETTE[(extra.index(address) if address in extra else 0) % len(PALETTE)]


def fmt(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def segments(points: list[tuple[int, float | None]], gap_ms: int = GAP_MS) -> list[list[tuple[int, float]]]:
    """Tramos continuos: un valor ausente o un hueco mayor a `gap_ms` corta la línea."""
    out: list[list[tuple[int, float]]] = []
    current: list[tuple[int, float]] = []
    previous_t = None
    for t, v in points:
        if v is None or (previous_t is not None and t - previous_t > gap_ms):
            if current:
                out.append(current)
            current = []
        if v is not None:
            current.append((t, v))
        previous_t = t
    if current:
        out.append(current)
    return out


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / count
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    first = math.ceil(lo / step) * step
    return [round(first + i * step, 10) for i in range(int((hi - first) / step) + 1)]


def parse_local(text: str) -> int:
    """'AAAA-MM-DD HH:MM' en hora local → ms UTC."""
    return int(datetime.strptime(text.strip(), LOCAL_FORMAT).astimezone().timestamp() * 1000)


def format_local(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000).strftime(LOCAL_FORMAT)


def reading_state(row: dict, now_ms: int) -> str:
    if not row["ok"]:
        return "Sin respuesta"
    if not row["valid"]:
        return "Inválida"
    if (now_ms - row["t_ms"]) / 1000 > STALE_S:
        return "Anterior"
    return "Válida"


# --------------------------------------------------------------- lectura viva
class SourceError(Exception):
    """La base no está disponible todavía o no se puede leer."""


@dataclass
class Snapshot:
    now_ms: int
    series: dict[int, dict]          # dirección → {"type": ..., "points": [(t_ms, valor|None)]}
    latest: dict[int, dict]          # dirección → última fila conocida
    run: dict | None
    last_ms: int | None


class LiveSource:
    """Tres consultas indexadas por refresco, sin trazas ni dependencias pesadas."""

    def __init__(self, db: Path) -> None:
        self.db = Path(db)
        self.conn: sqlite3.Connection | None = None
        self._run_id: str | None = None
        self._run_meta: dict = {}

    def _connection(self) -> sqlite3.Connection:
        if self.conn is None:
            if not self.db.exists():
                raise SourceError("Esperando la adquisición: todavía no existe la base de datos")
            conn = sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                conn.close()
                hint = ": ejecute python -m smp22x migrate" if version == 1 else ""
                raise SourceError(f"La base está en el esquema {version}{hint}")
            self.conn = conn
        return self.conn

    @staticmethod
    def _row(r: tuple) -> dict:
        address, sensor_type, t_ms, ok, valid, irr, lw, temp, volt, rtt, status, error = r
        pyrg = sensor_type == reg.PYRGEOMETER
        value = (lw if pyrg else irr) if valid else None
        return {"address": address, "sensor_type": sensor_type, "t_ms": t_ms, "ok": bool(ok), "valid": bool(valid),
                "value": value, "temp": temp, "volt": volt, "rtt": rtt, "status": status, "error": error}

    def snapshot(self, now_ms: int, window_ms: int = WINDOW_MS) -> Snapshot:
        try:
            c = self._connection()
            rows = [self._row(r) for r in c.execute(SELECT_SINCE, (now_ms - window_ms,))]
            last_ms = c.execute("SELECT max(t_ms) FROM samples").fetchone()[0]
            recent = rows
            if not rows and last_ms is not None:  # adquisición detenida: últimas lecturas conocidas
                recent = [self._row(r) for r in c.execute(SELECT_SINCE, (last_ms - 10_000,))]
            run = c.execute("SELECT id, state, metadata FROM runs ORDER BY pk DESC LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            self.close()
            raise SourceError(f"Base de datos no disponible: {exc}") from exc
        series: dict[int, dict] = {}
        for r in rows:
            series.setdefault(r["address"], {"type": r["sensor_type"], "points": []})["points"].append((r["t_ms"], r["value"]))
        latest: dict[int, dict] = {}
        for r in recent:
            latest[r["address"]] = r
        info = None
        if run is not None:
            if run[0] != self._run_id:  # los metadatos solo se leen al cambiar de corrida
                self._run_id = run[0]
                meta = json.loads(run[2])
                self._run_meta = {"label": (meta.get("config") or {}).get("label") or "",
                                  "simulated": bool(meta.get("simulated")),
                                  "addresses": (meta.get("config") or {}).get("addresses") or []}
            info = {"id": run[0], "state": run[1], **self._run_meta}
        return Snapshot(now_ms, series, latest, info, last_ms)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


# ------------------------------------------------------------------ exportación
class ExportError(Exception):
    pass


def _check(result):
    from .result import Err
    if isinstance(result, Err):
        raise ExportError(f"{result.error.code}: {result.error.message}")
    return result.value


def _sample_pages(db: Path, archive_dir: Path, start_ms: int, end_ms: int, addresses: list[int] | None):
    from .storage import Reader
    reader = Reader(db)
    manifest = _check(reader.manifest())
    pruned = [d for d in manifest.values() if d["pruned_ms"] is not None]
    if any(_day_overlaps(d["day"], start_ms, end_ms) for d in pruned):
        from .history import iter_samples  # pyarrow: solo si hay días que ya no están en la base
        yield from iter_samples(reader, archive_dir, start_ms, end_ms, addresses)
        return
    after = None
    while True:
        page = _check(reader.range_samples(start_ms, end_ms, addresses, after, 5000))
        if page["items"]:
            yield page["items"]
        after = page["next"]
        if after is None:
            return


def _day_overlaps(day: str, start_ms: int, end_ms: int) -> bool:
    from datetime import date, timezone
    start = int(datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc).timestamp() * 1000)
    return start < end_ms and start + DAY_MS > start_ms


def export_csv(db: Path, archive_dir: Path, path: Path, start_ms: int, end_ms: int, addresses: list[int] | None,
               resolution: str, include_invalid: bool, progress: Callable[[int], None] | None = None) -> int:
    """CSV con las columnas clásicas (raw_1s.csv o minute_avg.csv). Devuelve las filas escritas.

    Escribe en `<archivo>.part` y renombra al terminar: un error no deja un CSV a medias.
    """
    if resolution not in MAX_SPAN_MS:
        raise ExportError("Resolución 1s o 1min")
    if end_ms <= start_ms:
        raise ExportError("El inicio debe ser anterior al fin")
    if end_ms - start_ms > MAX_SPAN_MS[resolution]:
        raise ExportError(f"Con {resolution} el máximo es {MAX_SPAN_MS[resolution] // DAY_MS} días")
    path = Path(path)
    temp = path.with_name(path.name + ".part")
    count = 0
    try:
        with open(temp, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if resolution == "1s":
                from .acquisition import RAW_COLUMNS, raw_row
                from .storage import record_to_sample
                writer.writerow(RAW_COLUMNS)
                for page in _sample_pages(db, archive_dir, start_ms, end_ms, addresses):
                    for r in page:
                        if include_invalid or r["valid"]:
                            writer.writerow(raw_row(r["cycle"], record_to_sample(r)))
                            count += 1
                    if progress:
                        progress(count)
            else:
                from .minutes import MINUTE_COLUMNS, MinuteRecord
                from .storage import Reader
                reader = Reader(db)
                writer.writerow(MINUTE_COLUMNS)
                lo = start_ms
                while lo < end_ms:
                    hi = min(end_ms, lo + DAY_MS)
                    for m in _check(reader.range_minutes(lo, hi, addresses))["items"]:
                        if include_invalid or m["n_valid"] > 0:
                            writer.writerow(MinuteRecord(
                                m["minute_ms"], m["address"], m["sensor_type"], m["n_samples"], m["n_valid"],
                                m["mean_wm2"], m["min_wm2"], m["max_wm2"], m["std_wm2"], m["net_mean_wm2"],
                                m["body_temp_c_mean"], m["complete"]).csv_row())
                            count += 1
                    lo = hi
                    if progress:
                        progress(count)
        os.replace(temp, path)
        return count
    except BaseException:
        if temp.exists():
            temp.unlink()
        raise


# -------------------------------------------------------------------- interfaz
@dataclass
class Line:
    label: str
    color: str
    points: list[tuple[int, float | None]]


class Chart:
    """Gráfico de 60 s en un Canvas: se redibuja entero cada segundo (decenas de ítems)."""

    LEFT, RIGHT, TOP, BOTTOM = 58, 14, 30, 24

    def __init__(self, parent, title: str, unit: str) -> None:
        import tkinter as tk
        from tkinter import font, ttk
        self.unit = unit
        self.frame = ttk.Frame(parent)
        ttk.Label(self.frame, text=f"{title} ({unit})", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(self.frame, height=210, background="white", highlightthickness=1,
                                highlightbackground="#dce3e3")
        self.canvas.pack(fill="both", expand=True, pady=(2, 0))
        self.small = font.nametofont("TkDefaultFont").copy()
        self.small.configure(size=8)
        self.lines: list[Line] = []
        self.now_ms = 0
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

    def draw(self, lines: list[Line], now_ms: int) -> None:
        self.lines, self.now_ms = lines, now_ms
        self.redraw()

    def _x(self, t: int, w: int) -> float:
        start = self.now_ms - WINDOW_MS
        return self.LEFT + (t - start) / WINDOW_MS * (w - self.LEFT - self.RIGHT)

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        if c.winfo_ismapped():
            w, h = c.winfo_width(), c.winfo_height()
        else:  # sin mapear (oculta o en tests): tamaño pedido
            w, h = int(c.cget("width")), int(c.cget("height"))
        if w < 80 or h < 60:
            return
        x = self.LEFT
        for line in self.lines:
            last = next((v for _, v in reversed(line.points) if v is not None), None)
            text = f"{line.label}  {fmt(last)}"
            c.create_line(x, 12, x + 14, 12, fill=line.color, width=3)
            c.create_text(x + 18, 12, text=text, anchor="w", fill="#3f565a", font=self.small)
            x += 30 + self.small.measure(text)
        values = [v for line in self.lines for _, v in line.points if v is not None]
        bottom = h - self.BOTTOM
        for k in range(0, 61, 15):
            px = self._x(self.now_ms - WINDOW_MS + k * 1000, w)
            c.create_line(px, self.TOP, px, bottom, fill="#f1f4f3")
            label = datetime.fromtimestamp((self.now_ms - WINDOW_MS + k * 1000) / 1000).strftime("%H:%M:%S") \
                if k in (0, 60) else f"−{60 - k} s"
            c.create_text(px, bottom + 12, text=label, fill="#7d8f92", font=self.small)
        if not values:
            c.create_text(w / 2, (self.TOP + bottom) / 2, text="Sin lecturas válidas en el último minuto",
                          fill="#87999c")
            return
        lo, hi = min(values), max(values)
        pad = max((hi - lo) * 0.12, abs(hi) * 0.005, 0.5)
        lo, hi = lo - pad, hi + pad

        def y(v: float) -> float:
            return self.TOP + (hi - v) / (hi - lo) * (bottom - self.TOP)

        for tick in nice_ticks(lo, hi):
            py = y(tick)
            c.create_line(self.LEFT, py, w - self.RIGHT, py, fill="#edf1f0")
            c.create_text(self.LEFT - 6, py, text=fmt(tick, 1 if abs(hi - lo) < 50 else 0), anchor="e",
                          fill="#7d8f92", font=self.small)
        for line in self.lines:
            for segment in segments(line.points):
                coords = [coord for t, v in segment for coord in (self._x(t, w), y(v))]
                if len(segment) == 1:  # una lectura válida aislada no puede quedar invisible
                    px, py = coords
                    c.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5, fill=line.color, outline=line.color)
                else:
                    c.create_line(*coords, fill=line.color, width=2)


class LightWindow:
    COLUMNS = (("sensor", "Sensor", 70), ("tipo", "Tipo", 120), ("valor", "Valor (W/m²)", 100),
               ("estado", "Estado", 110), ("hace", "Hace (s)", 70), ("temp", "T cuerpo (°C)", 95),
               ("volt", "Alimentación (V)", 110), ("rtt", "RTT (ms)", 75), ("flags", "Flags", 220))

    def __init__(self, root, source: LiveSource, db: Path, archive_dir: Path,
                 clock: Callable[[], float] = time.time) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.root, self.source, self.db, self.archive_dir, self.clock = root, source, Path(db), Path(archive_dir), clock
        self.addresses: list[int] = []
        self.messages: queue.Queue = queue.Queue()
        self.refreshes = 0
        root.title("Radiometría · ventana liviana")
        root.minsize(860, 640)
        body = ttk.Frame(root, padding=10)
        body.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Conectando con la base de datos…")
        ttk.Label(body, textvariable=self.status, foreground="#4d6669").pack(anchor="w", pady=(0, 6))
        charts = ttk.Frame(body)
        charts.pack(fill="both", expand=True)
        charts.columnconfigure((0, 1), weight=1, uniform="charts")
        charts.rowconfigure(0, weight=1)
        self.shortwave = Chart(charts, "Onda corta · piranómetros", "W/m²")
        self.longwave = Chart(charts, "Onda larga descendente · pirgeómetro", "W/m²")
        self.shortwave.frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.longwave.frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(body, text="Última lectura", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(10, 2))
        self.table = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS], show="headings", height=4)
        for key, title, width in self.COLUMNS:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="w", stretch=key == "flags")
        self.table.tag_configure("bad", foreground="#a2493a")
        self.table.tag_configure("old", foreground="#8a7a4a")
        self.table.pack(fill="x")
        self._build_export(body)
        root.bind("<Map>", self._on_map)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ----------------------------------------------------------- exportación
    def _build_export(self, body) -> None:
        import tkinter as tk
        from tkinter import ttk
        box = ttk.LabelFrame(body, text="Descargar datos (CSV)", padding=8)
        box.pack(fill="x", pady=(12, 0))
        now = int(self.clock() * 1000)
        self.start_var = tk.StringVar(value=format_local(now - 3_600_000))
        self.end_var = tk.StringVar(value=format_local(now))
        self.resolution = tk.StringVar(value="1min")
        self.include_invalid = tk.BooleanVar(value=True)
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="Desde").pack(side="left")
        ttk.Entry(row, textvariable=self.start_var, width=17).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="Hasta").pack(side="left")
        ttk.Entry(row, textvariable=self.end_var, width=17).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="AAAA-MM-DD HH:MM, hora local", foreground="#7d8f92").pack(side="left")
        presets = ttk.Frame(box)
        presets.pack(fill="x", pady=(6, 0))
        for label, key in (("Última hora", "hour"), ("Hoy", "today"), ("Ayer", "yesterday"),
                           ("Últimas 24 h", "24h"), ("Últimos 7 días", "7d")):
            ttk.Button(presets, text=label, command=lambda k=key: self.preset(k)).pack(side="left", padx=(0, 4))
        options = ttk.Frame(box)
        options.pack(fill="x", pady=(6, 0))
        ttk.Radiobutton(options, text="Muestras de 1 s (hasta 31 días)", value="1s", variable=self.resolution).pack(side="left")
        ttk.Radiobutton(options, text="Promedios de 1 min (hasta 366 días)", value="1min",
                        variable=self.resolution).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(options, text="Incluir lecturas inválidas", variable=self.include_invalid).pack(side="left", padx=(16, 0))
        self.instrument_box = ttk.Frame(box)
        self.instrument_box.pack(fill="x", pady=(6, 0))
        self.instrument_vars: dict[int, tk.BooleanVar] = {}
        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(8, 0))
        self.save_button = ttk.Button(actions, text="Guardar CSV…", command=self.save_csv)
        self.save_button.pack(side="left")
        self.export_status = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.export_status, foreground="#4d6669").pack(side="left", padx=10)

    def preset(self, key: str) -> None:
        now = datetime.fromtimestamp(self.clock())
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        start = {"hour": now.timestamp() - 3600, "today": midnight.timestamp(), "24h": now.timestamp() - 86400,
                 "7d": now.timestamp() - 7 * 86400, "yesterday": midnight.timestamp() - 86400}[key]
        if key == "yesterday":
            end = midnight
        self.start_var.set(format_local(int(start * 1000)))
        self.end_var.set(format_local(int(end.timestamp() * 1000)))
        self.resolution.set("1s" if end.timestamp() - start <= 6 * 3600 else "1min")

    def _sync_instruments(self, addresses: list[int]) -> None:
        import tkinter as tk
        from tkinter import ttk
        if addresses == self.addresses:
            return
        self.addresses = addresses
        for child in self.instrument_box.winfo_children():
            child.destroy()
        ttk.Label(self.instrument_box, text="Instrumentos:").pack(side="left")
        for address in addresses:
            var = self.instrument_vars.setdefault(address, tk.BooleanVar(value=True))
            ttk.Checkbutton(self.instrument_box, text=f"#{address}", variable=var).pack(side="left", padx=(6, 0))

    def export_request(self) -> dict:
        """Valida el formulario. Lanza ExportError con un mensaje para mostrar."""
        try:
            start, end = parse_local(self.start_var.get()), parse_local(self.end_var.get())
        except ValueError:
            raise ExportError("Fechas con formato AAAA-MM-DD HH:MM")
        chosen = [a for a in self.addresses if self.instrument_vars.get(a) is None or self.instrument_vars[a].get()]
        if self.addresses and not chosen:
            raise ExportError("Elija al menos un instrumento")
        resolution = self.resolution.get()
        if end <= start:
            raise ExportError("El inicio debe ser anterior al fin")
        if end - start > MAX_SPAN_MS[resolution]:
            raise ExportError(f"Con {resolution} el máximo es {MAX_SPAN_MS[resolution] // DAY_MS} días")
        return {"start_ms": start, "end_ms": end, "resolution": resolution,
                "addresses": chosen if chosen and len(chosen) < len(self.addresses) else None,
                "include_invalid": self.include_invalid.get()}

    def save_csv(self, path: str | None = None) -> None:
        from tkinter import filedialog
        try:
            request = self.export_request()
        except ExportError as exc:
            self.export_status.set(str(exc))
            return
        if path is None:
            stamp = f"{format_local(request['start_ms'])}_{format_local(request['end_ms'])}".replace(":", "").replace(" ", "T")
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv",
                                                filetypes=[("CSV", "*.csv")],
                                                initialfile=f"radiometria_{stamp}_{request['resolution']}.csv")
            if not path:
                return
        self.save_button.state(["disabled"])
        self.export_status.set("Exportando…")

        def work():
            try:
                rows = export_csv(self.db, self.archive_dir, Path(path), progress=lambda n: self.messages.put(("progress", n)),
                                  **request)
                self.messages.put(("done", rows, path))
            except Exception as exc:  # se muestra en la ventana; la adquisición no se ve afectada
                self.messages.put(("error", str(exc)))

        threading.Thread(target=work, name="exportacion-csv", daemon=True).start()
        self.root.after(200, self._poll_export)

    def _poll_export(self) -> None:
        finished = False
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if message[0] == "progress":
                self.export_status.set(f"Exportando… {message[1]:,} filas".replace(",", "."))
            elif message[0] == "done":
                self.export_status.set(f"Guardado: {message[1]:,} filas en {message[2]}".replace(",", "."))
                finished = True
            else:
                self.export_status.set(f"No se pudo exportar: {message[1]}")
                finished = True
        if finished:
            self.save_button.state(["!disabled"])
        else:
            self.root.after(200, self._poll_export)

    # ------------------------------------------------------------ refresco
    def minimized(self) -> bool:
        return self.root.state() == "iconic"

    def _on_map(self, event) -> None:
        if event.widget is self.root:
            self.refresh()  # al restaurar: el último minuto se vuelve a leer de la base

    def tick(self) -> None:
        if not self.minimized():
            self.refresh()
        self.root.after(REFRESH_MS, self.tick)

    def refresh(self) -> None:
        now = int(self.clock() * 1000)
        try:
            snap = self.source.snapshot(now)
        except SourceError as exc:
            self.status.set(str(exc))
            return
        self.refreshes += 1
        addresses = sorted(set(snap.series) | set(snap.latest) | set((snap.run or {}).get("addresses") or []))
        self._sync_instruments(addresses)
        short, long = [], []
        for address in addresses:
            serie = snap.series.get(address) or {"type": (snap.latest.get(address) or {}).get("sensor_type"), "points": []}
            pyrg = serie["type"] == reg.PYRGEOMETER
            line = Line(f"#{address} Ld↓" if pyrg else f"#{address}", color_for(address, addresses), serie["points"])
            (long if pyrg else short).append(line)
        self.shortwave.draw(short, now)
        self.longwave.draw(long, now)
        for item in self.table.get_children():
            if int(item) not in snap.latest:
                self.table.delete(item)
        for address, row in sorted(snap.latest.items()):
            state = reading_state(row, now)
            values = (f"#{address}", "pirgeómetro (Ld↓)" if row["sensor_type"] == reg.PYRGEOMETER else "piranómetro",
                      fmt(row["value"]), state, fmt((now - row["t_ms"]) / 1000, 0), fmt(row["temp"]), fmt(row["volt"]),
                      fmt(row["rtt"], 0), ", ".join(reg.decode_status(row["status"])) if row["status"] else (row["error"] or "—"))
            tags = ("bad",) if state in ("Sin respuesta", "Inválida") else ("old",) if state == "Anterior" else ()
            if self.table.exists(str(address)):
                self.table.item(str(address), values=values, tags=tags)
            else:
                self.table.insert("", "end", iid=str(address), values=values, tags=tags)
        run = snap.run or {}
        last = f"última lectura {datetime.fromtimestamp(snap.last_ms / 1000).strftime('%H:%M:%S')}" if snap.last_ms else "sin lecturas"
        kind = "SIMULADO" if run.get("simulated") else "HARDWARE"
        state = "en curso" if run.get("state") == "running" else "detenida"
        self.status.set(f"Corrida «{run.get('label') or 'sin etiqueta'}» · {kind} · {state} · {last} · "
                        "los datos los guarda la adquisición; esta ventana solo los muestra")

    def close(self) -> None:
        self.source.close()
        self.root.destroy()


def run(db: Path, archive_dir: Path) -> None:
    try:
        import tkinter as tk
    except ImportError as exc:  # Python sin Tcl/Tk
        raise ValueError("Tkinter no está disponible: instale Python con la opción «tcl/tk and IDLE»") from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise ValueError(f"No se pudo abrir la ventana: {exc}") from exc
    root.geometry("1180x780")
    window = LightWindow(root, LiveSource(db), db, archive_dir)
    window.tick()
    root.mainloop()

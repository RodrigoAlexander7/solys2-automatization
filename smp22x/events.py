"""Eventos de adquisición: cambios de estado observados en el bus, no mediciones.

Se derivan de las muestras en el proceso de adquisición y se guardan en la misma
transacción que el ciclo. Un flag que alterna cada segundo no debe llenar la base:
los cambios de la palabra de estado se emiten como máximo una vez por ventana y
por dirección, y al vencer la ventana se emite el estado vigente con el número de
cambios intermedios suprimidos. Así el último evento siempre coincide con el
último estado observado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import registers as reg
from .sensor import Sample

FLAG_WINDOW_MS = 10_000      # mínimo entre eventos de flags de una misma dirección
SILENT_AFTER_FAILURES = 5    # igual que el backoff de adquisición

INFO, WARNING, ERROR = "info", "warning", "error"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def to_ms(moment: datetime) -> int:
    """Milisegundos UTC enteros, truncados como isoformat(timespec="milliseconds")."""
    return (moment.astimezone(timezone.utc) - EPOCH) // timedelta(milliseconds=1)


def from_ms(t_ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=t_ms)


def iso_ms(t_ms: int) -> str:
    return from_ms(t_ms).isoformat(timespec="milliseconds")


def now_ms() -> int:
    return to_ms(datetime.now(timezone.utc))


def describe(kind: str, detail: dict, address: int | None) -> str:
    """Texto breve en español para la línea de tiempo."""
    d, who = detail or {}, f"#{address}" if address is not None else ""
    if kind == "flags_changed":
        parts = []
        if d.get("set"):
            parts.append("activados " + ", ".join(d["set"]))
        if d.get("cleared"):
            parts.append("borrados " + ", ".join(d["cleared"]))
        text = "; ".join(parts) or ("sin flags" if not d.get("names") else ", ".join(d["names"]))
        extra = f" ({d['suppressed']} cambios intermedios)" if d.get("suppressed") else ""
        return f"{who} flags: {text}{extra}"
    if kind == "clock_check":
        return f"Reloj: desfase {d.get('offset_s', 0):+.3f} s respecto de {d.get('server')} (retardo {d.get('delay_s', 0):.3f} s)"
    templates = {
        "run_started": "Inicio de corrida «{label}»",
        "run_finished": "Fin de corrida ({reason})",
        "run_interrupted": "Corrida {run_id} interrumpida: no terminó limpiamente",
        "port_lost": "Puerto perdido: {error}",
        "port_recovered": "Puerto recuperado tras {downtime_s} s",
        "sensor_silent": "{who} sin respuesta ({failures} fallos seguidos)",
        "sensor_recovered": "{who} vuelve a responder",
        "mode_changed": "{who} en modo {mode_name}",
        "flags_flapping": "{who} flags alternando ({changes} cambios en la ventana)",
        "error_cleared": "{who}: IO_CLEAR_ERROR enviado",
        "clock_unavailable": "Reloj: sin referencia NTP ({error})",
        "archive_written": "Archivo Parquet del {day} ({rows} filas)",
        "archive_pruned": "Día {day} retirado de la base activa; queda en Parquet",
        "archive_failed": "Error de archivo {day}: {error}",
    }
    template = templates.get(kind)
    if template is None:
        return f"{who} {kind}".strip()
    try:
        return template.format(who=who, **{k: ("" if v is None else v) for k, v in d.items()})
    except (KeyError, ValueError):
        return f"{who} {kind}".strip()


@dataclass(frozen=True)
class Event:
    t_ms: int
    kind: str
    severity: str
    address: int | None = None
    detail: dict = field(default_factory=dict)


def _flag_severity(previous: int | None, status: int) -> str:
    if status & reg.INVALID_DATA_MASK:
        return ERROR
    newly_set = status & ~(previous or 0)
    return WARNING if newly_set else INFO


@dataclass
class _AddressState:
    failures: int = 0
    silent: bool = False
    last_mode: int | None = None
    emitted_status: int | None = None
    emitted_ms: int = -FLAG_WINDOW_MS
    current_status: int | None = None
    suppressed: int = 0


class EventTracker:
    def __init__(self, window_ms: int = FLAG_WINDOW_MS, silent_after: int = SILENT_AFTER_FAILURES):
        self.window_ms = window_ms
        self.silent_after = silent_after
        self.state: dict[int, _AddressState] = {}

    def observe(self, s: Sample) -> list[Event]:
        st = self.state.setdefault(s.address, _AddressState())
        t = to_ms(s.time_utc)
        events: list[Event] = []
        if not s.ok:
            st.failures += 1
            if st.failures == self.silent_after and not st.silent:
                st.silent = True
                events.append(Event(t, "sensor_silent", WARNING, s.address,
                                    {"failures": st.failures, "error": s.error, "error_code": s.error_code}))
            return events
        if st.silent:
            events.append(Event(t, "sensor_recovered", INFO, s.address, {"after_failures": st.failures}))
        st.silent, st.failures = False, 0
        if s.mode is not None and s.mode != st.last_mode:
            if st.last_mode is not None or s.mode != reg.MODE_NORMAL:
                events.append(Event(t, "mode_changed", INFO if s.mode == reg.MODE_NORMAL else WARNING, s.address,
                                    {"mode": s.mode, "mode_name": reg.OPERATIONAL_MODES.get(s.mode, str(s.mode)),
                                     "previous": st.last_mode}))
            st.last_mode = s.mode
        if s.status is not None:
            if st.current_status is not None and s.status != st.current_status:
                st.suppressed += 1
            st.current_status = s.status
            events.extend(self._flags(s.address, st, t))
        return events

    def _flags(self, address: int, st: _AddressState, t: int) -> list[Event]:
        if st.current_status is None:
            return []
        if st.emitted_status is None and st.current_status == 0:
            st.emitted_status, st.suppressed = 0, 0  # línea base sin flags: nada que informar
            return []
        if t - st.emitted_ms < self.window_ms:
            return []
        changed = st.current_status != st.emitted_status
        if not changed and st.suppressed == 0:
            return []
        previous, status = st.emitted_status, st.current_status
        # El cambio que lleva al estado emitido no cuenta como suprimido.
        suppressed = max(0, st.suppressed - (1 if changed else 0))
        st.emitted_status, st.emitted_ms, st.suppressed = status, t, 0
        if not changed:
            return [Event(t, "flags_flapping", WARNING, address,
                          {"status": status, "names": reg.decode_status(status), "changes": suppressed})]
        before = previous or 0
        return [Event(t, "flags_changed", _flag_severity(previous, status), address, {
            "status": status, "previous": previous, "names": reg.decode_status(status),
            "set": reg.decode_status(status & ~before), "cleared": reg.decode_status(before & ~status),
            "suppressed": suppressed})]

    def tick(self, t_ms: int) -> list[Event]:
        """Emite estados pendientes cuando vence la ventana, aunque no lleguen muestras nuevas."""
        events: list[Event] = []
        for address, st in sorted(self.state.items()):
            events.extend(self._flags(address, st, t_ms))
        return events

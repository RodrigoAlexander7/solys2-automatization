"""Reducción de series para dibujar (R28): nunca más puntos que los que entran en pantalla.

Cada serie (dirección y rol) se divide en tramos de duración B. Por tramo quedan
dos filas, el mínimo y el máximo en el orden en que ocurrieron, ubicadas al
inicio y a la mitad del tramo, de modo que los picos (nubes, sombras) siguen
visibles. Un tramo sin lecturas válidas devuelve una fila vacía, que corta la
línea: un hueco nunca se une ni se rellena. Solo afecta al dibujo; las
descargas siempre llevan todas las filas.
"""
from __future__ import annotations

import math
from collections import defaultdict

SEVERITY = {"na": 0, "ok": 1, "suspect": 2, "bad": 3}


def _worst(codes) -> str:
    return max(codes, key=lambda c: SEVERITY.get(c, 0), default="na")


def decimate(rows: list[dict], *, time_key: str, value_key: str, valid_key: str, start_ms: int, end_ms: int,
             max_points: int, base_step_ms: int, offset_ms: int = 0,
             extra_keys: tuple[str, ...] = ("dni_est_wm2",)) -> tuple[list[dict], int, bool]:
    """Devuelve (filas, duración del tramo en ms, si se redujo).

    `offset_ms` corrige el desplazamiento que el cliente suma al tiempo (30 s en minutos).
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["address"], r.get("role"))].append(r)
    if max_points <= 0 or all(len(g) <= max_points for g in groups.values()):
        return rows, base_step_ms, False
    buckets = max(1, max_points // 2)
    bucket_ms = max(base_step_ms, math.ceil((end_ms - start_ms) / buckets / base_step_ms) * base_step_ms)
    count = math.ceil((end_ms - start_ms) / bucket_ms)

    def is_valid(r: dict) -> bool:
        v = r.get(valid_key)
        return (v > 0 if isinstance(v, int) and not isinstance(v, bool) else bool(v)) and r.get(value_key) is not None

    out: list[dict] = []
    for items in groups.values():
        by_bucket: dict[int, list[dict]] = defaultdict(list)
        for r in items:
            by_bucket[int((r[time_key] + offset_ms - start_ms) // bucket_ms)].append(r)
        template = items[0]
        for k in range(count):
            t0 = start_ms + k * bucket_ms - offset_ms
            members = by_bucket.get(k, [])
            valid = [r for r in members if is_valid(r)]
            qc = _worst(r.get("qc") for r in members) if members else "na"
            if not valid:
                out.append({**template, time_key: t0, value_key: None, valid_key: False if valid_key == "valid" else 0,
                            "qc": qc, "qc_failed": [], **{key: None for key in extra_keys}})
                continue
            lo = min(valid, key=lambda r: r[value_key])
            hi = max(valid, key=lambda r: r[value_key])
            first, second = sorted((lo, hi), key=lambda r: r[time_key])
            failed = sorted({t for r in members for t in r.get("qc_failed") or []})
            extras = {key: [r[key] for r in members if r.get(key) is not None] for key in extra_keys}
            for position, source, pick in ((t0, first, min), (t0 + bucket_ms // 2, second, max)):
                out.append({**source, time_key: position, "qc": qc, "qc_failed": failed,
                            **{key: (pick(values) if values else None) for key, values in extras.items()}})
    out.sort(key=lambda r: (r[time_key], r["address"]))
    return out, bucket_ms, True

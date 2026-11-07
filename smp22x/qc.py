"""Pruebas de calidad: BSRN (Long y Dutton, 2010) y pruebas del proyecto.

Las pruebas nunca modifican un valor: devuelven, por elemento, "ok", "fail" o
"na" (no aplica). Funcionan con arreglos numpy; NaN representa un valor ausente
o inválido. Los límites BSRN usan Sa = S0/R² y μ0 = max(cos z, 0).

Fuente BSRN: C. N. Long y E. G. Dutton, *BSRN Global Network recommended QC
tests, V2.0* (2010). La prueba de sombreado y la comparación entre instrumentos
del mismo rol, y sus umbrales, son decisiones de este proyecto.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

OK, FAIL, NA = "ok", "fail", "na"
GHI, DHI, LWD, SHADE_OFF = "GHI", "DHI", "LWD", "SHADE_OFF"
ROLE_LABELS = {
    GHI: "Global (GHI)", DHI: "Difusa (DHI)", LWD: "Onda larga descendente (Ld↓)",
    SHADE_OFF: "Con bola de sombra · SOLYS2 inactivo",
}
TEST_LABELS = {
    "ppl": "BSRN físicamente posible", "erl": "BSRN extremadamente raro",
    "ratio": "BSRN cociente difusa/global", "shading": "Sombreado (proyecto)",
    "comparison": "Comparación entre iguales (proyecto)",
}

# (límite inferior, coeficiente, exponente, constante): superior = coef·Sa·μ0^exp + const.
PPL = {GHI: (-4.0, 1.5, 1.2, 100.0), DHI: (-4.0, 0.95, 1.2, 50.0)}
ERL = {GHI: (-2.0, 1.2, 1.2, 50.0), DHI: (-2.0, 0.75, 1.2, 30.0)}
LWD_PPL = (40.0, 700.0)
LWD_ERL = (60.0, 500.0)
RATIO_MIN_GHI = 50.0


@dataclass(frozen=True)
class Thresholds:
    comparison_pct: float = 5.0
    comparison_min_wm2: float = 100.0
    shading_kt_min: float = 0.6
    shading_ratio_max: float = 0.8

    def as_dict(self) -> dict:
        return asdict(self)


def _codes(applicable: np.ndarray, failed: np.ndarray) -> np.ndarray:
    out = np.full(applicable.shape, NA, dtype=object)
    out[applicable] = OK
    out[applicable & failed] = FAIL
    return out


def limits(role: str, sa: np.ndarray, mu0: np.ndarray, table: dict) -> tuple[np.ndarray, np.ndarray]:
    low, coef, power, const = table[role]
    return np.full(np.shape(mu0), low), coef * sa * mu0 ** power + const


def range_test(role: str, values, sa=None, mu0=None, solar_known: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Límites físicamente posibles y extremadamente raros. Devuelve (ppl, erl)."""
    v = np.asarray(values, dtype=float)
    present = ~np.isnan(v)
    if role == LWD:
        ppl = _codes(present, (v < LWD_PPL[0]) | (v > LWD_PPL[1]))
        erl = _codes(present, (v < LWD_ERL[0]) | (v > LWD_ERL[1]))
        return ppl, erl
    if role not in (GHI, DHI) or not solar_known:
        none = np.full(v.shape, NA, dtype=object)
        return none, none.copy()
    sa, mu0 = np.asarray(sa, dtype=float), np.asarray(mu0, dtype=float)
    lo, hi = limits(role, sa, mu0, PPL)
    ppl = _codes(present, (v < lo) | (v > hi))
    lo, hi = limits(role, sa, mu0, ERL)
    erl = _codes(present, (v < lo) | (v > hi))
    return ppl, erl


def ratio_test(ghi, dhi, zenith) -> np.ndarray:
    """BSRN: DHI/GHI < 1,05 con z < 75° y < 1,10 con 75° ≤ z < 93°, solo con GHI > 50 W/m²."""
    g, d, z = (np.asarray(x, dtype=float) for x in (ghi, dhi, zenith))
    applicable = ~np.isnan(g) & ~np.isnan(d) & ~np.isnan(z) & (z < 93) & (g > RATIO_MIN_GHI)
    limit = np.where(z < 75, 1.05, 1.10)
    with np.errstate(invalid="ignore", divide="ignore"):
        failed = d / g >= limit
    return _codes(applicable, failed)


def shading_test(ghi, dhi, zenith, sa, mu0, thresholds: Thresholds) -> np.ndarray:
    """Proyecto: con sol alto y cielo bastante despejado, la difusa no puede acercarse a la global."""
    g, d, z, s, m = (np.asarray(x, dtype=float) for x in (ghi, dhi, zenith, sa, mu0))
    with np.errstate(invalid="ignore", divide="ignore"):
        kt = g / (s * m)
        applicable = (~np.isnan(g) & ~np.isnan(d) & ~np.isnan(z) & (z < 75) & (g > RATIO_MIN_GHI)
                      & (kt > thresholds.shading_kt_min))
        failed = d / g > thresholds.shading_ratio_max
    return _codes(applicable, failed)


def comparison_test(a, b, thresholds: Thresholds) -> np.ndarray:
    """Proyecto: diferencia relativa entre dos instrumentos del mismo rol."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    mean = (x + y) / 2
    applicable = ~np.isnan(x) & ~np.isnan(y) & (mean > thresholds.comparison_min_wm2)
    with np.errstate(invalid="ignore", divide="ignore"):
        failed = np.abs(x - y) / mean * 100 > thresholds.comparison_pct
    return _codes(applicable, failed)


def clearness_index(ghi, sa, mu0) -> np.ndarray:
    g, s, m = (np.asarray(x, dtype=float) for x in (ghi, sa, mu0))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(m > 0, g / (s * m), np.nan)


def estimated_dni(ghi, dhi, zenith) -> np.ndarray:
    """DNI derivada (GHI − DHI)/cos z, solo con z < 80°. No es una medición."""
    g, d, z = (np.asarray(x, dtype=float) for x in (ghi, dhi, zenith))
    cos_z = np.cos(np.radians(z))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(z < 80, (g - d) / cos_z, np.nan)


def summary(results: dict[str, str]) -> str:
    """bad: falla física; suspect: otra falla; ok: alguna prueba aplicó; na: ninguna."""
    if results.get("ppl") == FAIL:
        return "bad"
    if FAIL in results.values():
        return "suspect"
    return OK if OK in results.values() else NA

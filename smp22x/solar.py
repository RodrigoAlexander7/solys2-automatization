"""Posición solar vectorizada (algoritmo NOAA, basado en Meeus) con refracción.

Precisión esperada: unas centésimas de grado entre 1950 y 2050, suficiente para
las pruebas de calidad BSRN, que trabajan con cos z. Se valida contra el vector de
referencia publicado de NREL SPA (Reda y Andreas, 2004). El tiempo de entrada es
UTC en milisegundos; no se aplica ΔT.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SOLAR_CONSTANT = 1361.0  # W/m², Kopp y Lean (2011)


@dataclass(frozen=True)
class SolarPosition:
    zenith: np.ndarray          # grados, con refracción
    azimuth: np.ndarray         # grados desde el norte, sentido horario
    distance_au: np.ndarray     # distancia Tierra-Sol
    mu0: np.ndarray             # max(cos z, 0)
    extraterrestrial: np.ndarray  # Sa = S0 / R², W/m²


def standard_pressure(altitude_m: float) -> float:
    """Presión de la atmósfera estándar a una altura, en hPa."""
    return 1013.25 * (1 - 2.25577e-5 * altitude_m) ** 5.25588


def refraction_deg(elevation: np.ndarray, pressure_hpa: float, temp_c: float) -> np.ndarray:
    """Refracción atmosférica (fórmula de Bennett escalada, como en SPA)."""
    e = np.asarray(elevation, dtype=float)
    correction = (pressure_hpa / 1010.0) * (283.0 / (273.0 + temp_c)) * 1.02 / (
        60.0 * np.tan(np.radians(e + 10.3 / (e + 5.11))))
    return np.where(e >= -0.83337, correction, 0.0)


def solar_position(t_ms, latitude: float, longitude: float, altitude_m: float = 0.0,
                   pressure_hpa: float | None = None, temp_c: float = 10.0) -> SolarPosition:
    t = np.atleast_1d(np.asarray(t_ms, dtype=float))
    jd = t / 86_400_000.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0
    l0 = np.mod(280.46646 + jc * (36000.76983 + jc * 0.0003032), 360.0)
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = np.radians(m)
    c = (np.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + np.sin(2 * mr) * (0.019993 - 0.000101 * jc) + np.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    true_anom = np.radians(m + c)
    distance = 1.000001018 * (1 - e * e) / (1 + e * np.cos(true_anom))
    omega = np.radians(125.04 - 1934.136 * jc)
    app_long = np.radians(true_long - 0.00569 - 0.00478 * np.sin(omega))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = np.radians(eps0 + 0.00256 * np.cos(omega))
    decl = np.arcsin(np.sin(eps) * np.sin(app_long))
    y = np.tan(eps / 2) ** 2
    l0r = np.radians(l0)
    eot = 4 * np.degrees(y * np.sin(2 * l0r) - 2 * e * np.sin(mr) + 4 * e * y * np.sin(mr) * np.cos(2 * l0r)
                         - 0.5 * y * y * np.sin(4 * l0r) - 1.25 * e * e * np.sin(2 * mr))
    minutes_utc = np.mod(t / 60_000.0, 1440.0)
    true_solar = np.mod(minutes_utc + eot + 4 * longitude, 1440.0)
    hour_angle = np.radians(true_solar / 4 - 180)
    lat = np.radians(latitude)
    cos_z = np.clip(np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(hour_angle), -1, 1)
    zenith_geo = np.degrees(np.arccos(cos_z))
    pressure = standard_pressure(altitude_m) if pressure_hpa is None else pressure_hpa
    zenith = zenith_geo - refraction_deg(90 - zenith_geo, pressure, temp_c)
    sin_z = np.sin(np.radians(zenith_geo))
    with np.errstate(invalid="ignore", divide="ignore"):
        cos_az = (np.sin(lat) * cos_z - np.sin(decl)) / (np.cos(lat) * sin_z)
    az = np.degrees(np.arccos(np.clip(np.nan_to_num(cos_az), -1, 1)))
    azimuth = np.where(hour_angle > 0, np.mod(az + 180, 360), np.mod(540 - az, 360))
    mu0 = np.maximum(np.cos(np.radians(zenith)), 0.0)
    return SolarPosition(zenith, azimuth, distance, mu0, SOLAR_CONSTANT / distance ** 2)

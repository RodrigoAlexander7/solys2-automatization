"""Interpretación de mediciones: rol de cada instrumento, geometría solar y QC.

Todo lo que se agrega aquí es derivado y va etiquetado; los valores medidos no
se modifican. Cada registro se interpreta con la versión de configuración
vigente en su instante, de modo que cambiar hoy la bola de sombra no altera la
lectura de días anteriores.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Callable, Iterable

import numpy as np

from . import qc
from . import registers as reg
from .observatory import ObservatoryConfig, default_config
from .solar import solar_position

QC_TESTS = ("ppl", "erl", "ratio", "shading", "comparison")


def role_of(sensor_type: str, shaded: bool, solys2_in_use: bool) -> str:
    if sensor_type == reg.PYRGEOMETER:
        return qc.LWD
    if not shaded:
        return qc.GHI
    return qc.DHI if solys2_in_use else qc.SHADE_OFF


@dataclass
class ConfigTimeline:
    """Versiones ordenadas por vigencia; antes de la primera rige la configuración por defecto."""
    starts: list[int]
    configs: list[ObservatoryConfig]
    ids: list[int | None]
    default: ObservatoryConfig

    @classmethod
    def build(cls, versions: list[dict], addresses: Iterable[int]) -> "ConfigTimeline":
        ordered = sorted(versions, key=lambda v: (v["valid_from_ms"], v["id"]))
        return cls([v["valid_from_ms"] for v in ordered],
                   [ObservatoryConfig.model_validate(v["config"]) for v in ordered],
                   [v["id"] for v in ordered], default_config(sorted(set(addresses))))

    def index(self, t_ms: int) -> int:
        return bisect.bisect_right(self.starts, t_ms) - 1

    def at(self, t_ms: int) -> ObservatoryConfig:
        i = self.index(t_ms)
        return self.default if i < 0 else self.configs[i]

    def version_id(self, t_ms: int) -> int | None:
        i = self.index(t_ms)
        return None if i < 0 else self.ids[i]

    def current(self, now_ms: int) -> ObservatoryConfig:
        return self.at(now_ms)


def _label(config: ObservatoryConfig, address: int) -> str:
    inst = config.instrument(address)
    return inst.label if inst and inst.label else f"#{address}"


def annotate(records: list[dict], timeline: ConfigTimeline, *, time_of: Callable[[dict], int],
             value_of: Callable[[dict], float | None], valid_of: Callable[[dict], bool],
             group_of: Callable[[dict], object]) -> list[dict]:
    """Agrega rol, posición solar, QC y derivados a cada registro (en sitio)."""
    n = len(records)
    if not n:
        return records
    times = np.array([time_of(r) for r in records], dtype=float)
    configs = [timeline.at(int(t)) for t in times]
    roles = []
    for r, cfg in zip(records, configs):
        inst = cfg.instrument(r["address"])
        roles.append(role_of(r["sensor_type"], bool(inst and inst.shaded), cfg.solys2_in_use))
    values = np.array([v if (valid_of(r) and (v := value_of(r)) is not None) else np.nan for r in records], dtype=float)
    zenith = np.full(n, np.nan)
    sa = np.full(n, np.nan)
    mu0 = np.full(n, np.nan)
    by_site: dict[tuple, list[int]] = defaultdict(list)
    for i, cfg in enumerate(configs):
        if cfg.site is not None:
            by_site[(cfg.site.latitude, cfg.site.longitude, cfg.site.altitude_m)].append(i)
    for (lat, lon, alt), idx in by_site.items():
        pos = solar_position(times[idx], lat, lon, alt)
        zenith[idx], sa[idx], mu0[idx] = pos.zenith, pos.extraterrestrial, pos.mu0
    solar_known = ~np.isnan(zenith)
    results = {t: np.full(n, qc.NA, dtype=object) for t in QC_TESTS}
    roles_arr = np.array(roles, dtype=object)
    for role in (qc.GHI, qc.DHI, qc.LWD):
        for known in (True, False):
            idx = np.where((roles_arr == role) & (solar_known == known))[0]
            if len(idx):
                ppl, erl = qc.range_test(role, values[idx], sa[idx], mu0[idx], solar_known=known)
                results["ppl"][idx], results["erl"][idx] = ppl, erl
    # Pruebas entre instrumentos del mismo ciclo (o del mismo minuto).
    groups: dict[object, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        groups[group_of(r)].append(i)
    dni = np.full(n, np.nan)
    ratio_value = np.full(n, np.nan)
    kt_value = np.full(n, np.nan)
    pair_rows, pair_a, pair_b = [], [], []
    cyc_ghi, cyc_dhi, cyc_z, cyc_sa, cyc_mu0, cyc_members = [], [], [], [], [], []
    for members in groups.values():
        ghi = [i for i in members if roles[i] == qc.GHI]
        dhi = [i for i in members if roles[i] == qc.DHI]
        by_role: dict[str, list[int]] = defaultdict(list)
        for i in members:
            if roles[i] in (qc.GHI, qc.DHI, qc.LWD):
                by_role[roles[i]].append(i)
        for same in by_role.values():
            for a, b in combinations(sorted(same, key=lambda i: records[i]["address"]), 2):
                pair_rows.append((a, b))
                pair_a.append(values[a])
                pair_b.append(values[b])
        ghi_valid = [values[i] for i in ghi if not np.isnan(values[i])]
        g = float(np.mean(ghi_valid)) if ghi_valid else np.nan
        for i in ghi:
            kt_value[i] = qc.clearness_index(g, sa[i], mu0[i])
        if dhi:
            d_i = dhi[0]
            cyc_ghi.append(g)
            cyc_dhi.append(values[d_i])
            cyc_z.append(zenith[d_i])
            cyc_sa.append(sa[d_i])
            cyc_mu0.append(mu0[d_i])
            cyc_members.append((d_i, ghi))
    if cyc_members:
        ratio = qc.ratio_test(cyc_ghi, cyc_dhi, cyc_z)
        shading = np.full(len(cyc_members), qc.NA, dtype=object)
        by_threshold: dict[tuple, list[int]] = defaultdict(list)
        for k, (d_i, _) in enumerate(cyc_members):
            t = configs[d_i].qc
            by_threshold[(t.shading_kt_min, t.shading_ratio_max)].append(k)
        arrays = [np.asarray(x, dtype=float) for x in (cyc_ghi, cyc_dhi, cyc_z, cyc_sa, cyc_mu0)]
        for (kt_min, ratio_max), ks in by_threshold.items():
            shading[ks] = qc.shading_test(*(a[ks] for a in arrays),
                                          qc.Thresholds(shading_kt_min=kt_min, shading_ratio_max=ratio_max))
        est = qc.estimated_dni(cyc_ghi, cyc_dhi, cyc_z)
        for k, (d_i, ghi) in enumerate(cyc_members):
            for i in (d_i, *ghi):
                results["ratio"][i] = _merge(results["ratio"][i], ratio[k])
                results["shading"][i] = _merge(results["shading"][i], shading[k])
            # Con global baja el cociente es ruido: mismo umbral que la prueba BSRN.
            g_k = cyc_ghi[k]
            ratio_value[d_i] = cyc_dhi[k] / g_k if not np.isnan(g_k) and g_k > qc.RATIO_MIN_GHI else np.nan
            dni[d_i] = est[k]
    if pair_rows:
        codes = np.full(len(pair_rows), qc.NA, dtype=object)
        by_pct: dict[float, list[int]] = defaultdict(list)
        for k, (a, _) in enumerate(pair_rows):
            by_pct[configs[a].qc.comparison_pct].append(k)
        va, vb = np.asarray(pair_a, dtype=float), np.asarray(pair_b, dtype=float)
        for pct, ks in by_pct.items():
            codes[ks] = qc.comparison_test(va[ks], vb[ks], qc.Thresholds(comparison_pct=pct))
        for (a, b), code in zip(pair_rows, codes):
            results["comparison"][a] = _merge(results["comparison"][a], code)
            results["comparison"][b] = _merge(results["comparison"][b], code)
    for i, r in enumerate(records):
        tests = {t: results[t][i] for t in QC_TESTS}
        notes = []
        if not valid_of(r):
            notes.append("dato inválido o ausente")
        elif roles[i] == qc.SHADE_OFF:
            notes.append("SOLYS2 inactivo: sombra no garantizada; sin pruebas de difusa")
        elif roles[i] in (qc.GHI, qc.DHI) and not solar_known[i]:
            notes.append("sitio sin configurar: pruebas solares no aplican")
        r.update(
            role=roles[i], role_label=qc.ROLE_LABELS[roles[i]], label=_label(configs[i], r["address"]),
            config_version=timeline.version_id(int(times[i])),
            solar_zenith_deg=None if np.isnan(zenith[i]) else round(float(zenith[i]), 3),
            kt=None if np.isnan(kt_value[i]) else round(float(kt_value[i]), 4),
            dhi_ghi_ratio=None if np.isnan(ratio_value[i]) else round(float(ratio_value[i]), 4),
            dni_est_wm2=None if np.isnan(dni[i]) else round(float(dni[i]), 2),
            qc={**tests, "summary": qc.summary(tests), "notes": notes},
        )
    return records


def _merge(current: str, new: str) -> str:
    """fail domina a ok, y ok a na."""
    if qc.FAIL in (current, new):
        return qc.FAIL
    if qc.OK in (current, new):
        return qc.OK
    return qc.NA


def annotate_samples(records: list[dict], timeline: ConfigTimeline) -> list[dict]:
    return annotate(records, timeline, time_of=lambda r: r["t_ms"], value_of=lambda r: r["measurement"],
                    valid_of=lambda r: bool(r["valid"]), group_of=lambda r: (r.get("run_id"), r["cycle"]))


def annotate_minutes(records: list[dict], timeline: ConfigTimeline) -> list[dict]:
    return annotate(records, timeline, time_of=lambda r: r["minute_ms"] + 30_000,
                    value_of=lambda r: r["mean_wm2"], valid_of=lambda r: r["n_valid"] > 0,
                    group_of=lambda r: r["minute_ms"])


def comparisons(latest: list[dict]) -> list[dict]:
    """Diferencias entre instrumentos del mismo rol en el mismo ciclo (registros anotados)."""
    out = []
    by_role: dict[str, list[dict]] = defaultdict(list)
    for r in latest:
        if r.get("role") in (qc.GHI, qc.DHI, qc.LWD):
            by_role[r["role"]].append(r)
    for role, rows in by_role.items():
        for a, b in combinations(sorted(rows, key=lambda r: r["address"]), 2):
            item = {"role": role, "a": a["address"], "b": b["address"], "difference_wm2": None,
                    "difference_pct": None}
            if a["valid"] and b["valid"] and a["cycle"] == b["cycle"] and a.get("run_id") == b.get("run_id"):
                diff = a["measurement"] - b["measurement"]
                mean = (a["measurement"] + b["measurement"]) / 2
                item.update(difference_wm2=diff, difference_pct=100 * diff / mean if mean > 100 else None)
            out.append(item)
    return out

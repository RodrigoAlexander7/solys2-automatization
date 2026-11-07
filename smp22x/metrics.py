"""Derived diagnostics, never replacements for sensor values."""
from datetime import datetime, timezone


def comparison(samples: list[dict], a: int | None = None, b: int | None = None) -> dict:
    """Diferencia entre dos piranómetros válidos del mismo ciclo (por defecto, los dos primeros)."""
    short = {s["address"]: s for s in samples if s["sensor_type"] == "pyranometer" and s["valid"]}
    addresses = sorted({s["address"] for s in samples if s["sensor_type"] == "pyranometer"})
    if a is None or b is None:
        if len(addresses) < 2:
            return {"difference_wm2": None, "difference_pct": None}
        a, b = addresses[0], addresses[1]
    first, second = short.get(a), short.get(b)
    if not first or not second or first["cycle"] != second["cycle"]:
        return {"difference_wm2": None, "difference_pct": None}
    difference = first["measurement"] - second["measurement"]
    mean = (first["measurement"] + second["measurement"]) / 2
    return {"difference_wm2": difference,
            "difference_pct": 100 * difference / mean if mean > 100 else None}


def age_s(timestamp: str) -> float:
    return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds())

"""Derived diagnostics, never replacements for sensor values."""
from datetime import datetime, timezone


def comparison(samples: list[dict]) -> dict:
    short = {s["address"]: s for s in samples if s["sensor_type"] == "pyranometer" and s["valid"]}
    a, b = short.get(3), short.get(4)
    if not a or not b or a["cycle"] != b["cycle"]:
        return {"difference_wm2": None, "difference_pct": None}
    difference = a["measurement"] - b["measurement"]
    mean = (a["measurement"] + b["measurement"]) / 2
    return {"difference_wm2": difference,
            "difference_pct": 100 * difference / mean if mean > 100 else None}


def age_s(timestamp: str) -> float:
    return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds())

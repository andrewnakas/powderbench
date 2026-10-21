"""Nearest-ski-area helper for the station generators (OpenSkiData, ODbL —
see data/skiareas/README.md)."""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def areas() -> list[dict]:
    with (ROOT / "data" / "skiareas" / "ski_areas_na.csv").open() as f:
        return [dict(r, lat=float(r["lat"]), lon=float(r["lon"]), lift_count=int(r["lift_count"]))
                for r in csv.DictReader(f)]


def km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def nearest(lat: float, lon: float, min_lifts: int = 1) -> tuple[dict | None, float]:
    best, dist = None, float("inf")
    for a in areas():
        if a["lift_count"] < min_lifts or abs(a["lat"] - lat) > 1 or abs(a["lon"] - lon) > 1.5:
            continue
        d = km(lat, lon, a["lat"], a["lon"])
        if d < dist:
            best, dist = a, d
    return best, dist


def label(name: str, lat: float, lon: float, within_km: float = 10.0) -> str:
    """Station display name: 'Atwater · 2 km from Alta' when a ski area is near."""
    a, d = nearest(lat, lon)
    if a is None or d > within_km:
        return name
    return f"{a['name']}" if d < 1 else f"{a['name']} ({d:.0f} km)"

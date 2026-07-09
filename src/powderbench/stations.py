"""Station registry: curated SNOTEL stations near iconic ski zones."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


def data_dir() -> Path:
    env = os.environ.get("POWDERBENCH_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class Station:
    station_id: str  # SNOTEL triplet, e.g. "766:UT:SNTL"
    slug: str
    name: str
    resort: str
    region: str
    state: str
    elevation_ft: float
    latitude: float
    longitude: float


@lru_cache(maxsize=1)
def load_stations() -> tuple[Station, ...]:
    path = data_dir() / "stations.yaml"
    raw = yaml.safe_load(path.read_text())
    return tuple(Station(**s) for s in raw["stations"])


def by_id() -> dict[str, Station]:
    return {s.station_id: s for s in load_stations()}


def station_ids() -> list[str]:
    return [s.station_id for s in load_stations()]

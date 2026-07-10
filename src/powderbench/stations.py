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
    station_id: str  # SNOTEL triplet ("766:UT:SNTL") or ERA5 point ("portillo:CL:ERA5")
    slug: str
    name: str
    resort: str
    region: str
    state: str
    elevation_ft: float
    latitude: float
    longitude: float
    league: str = "stations"
    truth_source: str | None = None  # override; defaults to the league's source


@lru_cache(maxsize=1)
def _all_stations() -> tuple[Station, ...]:
    path = data_dir() / "stations.yaml"
    raw = yaml.safe_load(path.read_text())
    return tuple(Station(**s) for s in raw["stations"])


def load_stations(league: str = "stations") -> tuple[Station, ...]:
    return tuple(s for s in _all_stations() if s.league == league)


def by_id(league: str | None = None) -> dict[str, Station]:
    pool = _all_stations() if league is None else load_stations(league)
    return {s.station_id: s for s in pool}


def station_ids(league: str = "stations") -> list[str]:
    return [s.station_id for s in load_stations(league)]

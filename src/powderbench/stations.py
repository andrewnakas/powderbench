"""Station registry.

Two sources, merged per league:
  - data/stations.yaml: hand-curated stations (the marquee ski zones). These
    are the league's *featured* set unless they say otherwise.
  - data/stations/<network>.csv: generated full-network lists (thousands of
    rows — see scripts/networks/). Default tier = network. A curated YAML
    entry with the same station_id always wins.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

import yaml

TIER_FEATURED = "featured"
TIER_NETWORK = "network"


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
    tier: str | None = None  # featured | network; None -> featured (curated YAML)
    network: str | None = None  # e.g. SNTL, CDEC, GHCND
    external_id: str | None = None  # the network's own id, when it differs
    # observer networks: truth for local day D is the report dated D+day_offset
    # (fitted per station against ERA5 — see networks/attribution.py)
    day_offset: int | None = None

    @property
    def featured(self) -> bool:
        return (self.tier or TIER_FEATURED) == TIER_FEATURED


_FIELDS = {f.name: f for f in fields(Station)}
_FLOATS = {"elevation_ft", "latitude", "longitude"}
_INTS = {"day_offset"}


def _from_row(row: dict, default_tier: str | None) -> Station:
    unknown = set(row) - set(_FIELDS)
    if unknown:
        raise ValueError(f"unknown station field(s) {sorted(unknown)} in {row.get('station_id')}")
    clean = {}
    for k, v in row.items():
        if v == "" or v is None:
            continue
        clean[k] = float(v) if k in _FLOATS else int(v) if k in _INTS else v
    clean.setdefault("tier", default_tier)
    return Station(**clean)


def network_files() -> list[Path]:
    return sorted((data_dir() / "stations").glob("*.csv"))


@lru_cache(maxsize=1)
def _all_stations() -> tuple[Station, ...]:
    raw = yaml.safe_load((data_dir() / "stations.yaml").read_text())
    curated = [_from_row(s, None) for s in raw["stations"]]
    seen = {s.station_id for s in curated}
    generated = []
    for path in network_files():
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                st = _from_row(row, TIER_NETWORK)
                if st.station_id in seen:
                    continue
                seen.add(st.station_id)
                generated.append(st)
    return tuple(curated + generated)


def clear_cache() -> None:
    """Forget the cached registries (tests, or after regenerating network files)."""
    _all_stations.cache_clear()
    from .leagues import load_leagues

    load_leagues.cache_clear()


def load_stations(league: str = "stations", featured_only: bool = False) -> tuple[Station, ...]:
    return tuple(
        s for s in _all_stations() if s.league == league and (s.featured or not featured_only)
    )


def by_id(league: str | None = None) -> dict[str, Station]:
    pool = _all_stations() if league is None else load_stations(league)
    return {s.station_id: s for s in pool}


def station_ids(league: str = "stations", featured_only: bool = False) -> list[str]:
    return [s.station_id for s in load_stations(league, featured_only)]

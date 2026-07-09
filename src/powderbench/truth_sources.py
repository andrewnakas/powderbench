"""Truth adapters: one QC'd daily-snowfall frame schema, multiple sources.

Every adapter returns rows of (station_id, date, snow24, valid, qc_flag), so
window_truth, scoring, rounds, climatology, and hindcast are source-agnostic.

- snotel: snow-depth deltas cross-checked against SWE (see truth.py) — real
  ground stations, used by the northern league.
- era5: ERA5 reanalysis daily snowfall via Open-Meteo's keyless archive API —
  model-analysis truth for the southern trial league (no public SH station
  API exists; see docs/DATA.md). QC is missing-data only.

A station's `truth_source` field can override its league default, which is how
real feeds (NIWA, DGA) get promoted per-station once they prove stable.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import snotel, truth
from .leagues import League
from .openmeteo import era5_daily_snowfall
from .stations import load_stations

QC_OK = truth.QC_OK
QC_MISSING = truth.QC_MISSING


def _era5_daily(station_ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    stations = [s for s in load_stations(league.name) if s.station_id in set(station_ids)]
    raw = era5_daily_snowfall(stations, begin, end)
    raw = raw.rename(columns={"snowfall_in": "snow24"})
    raw["valid"] = raw["snow24"].notna()
    raw["qc_flag"] = raw["valid"].map({True: QC_OK, False: QC_MISSING})
    return raw[["station_id", "date", "snow24", "valid", "qc_flag"]]


def _snotel_daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    # depth deltas need the prior day's reading
    from datetime import timedelta

    obs = snotel.fetch_daily(station_ids, begin - timedelta(days=1), end)
    daily = truth.daily_snowfall(obs)
    return daily[(daily["date"] >= begin) & (daily["date"] <= end)].reset_index(drop=True)


def daily_truth(league: League, station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """QC'd daily snowfall for [begin, end], dispatched by truth source.
    Stations with a per-station override are fetched from their own source."""
    overrides: dict[str, list[str]] = {}
    for s in load_stations(league.name):
        if s.station_id in set(station_ids):
            source = s.truth_source or league.truth_source
            overrides.setdefault(source, []).append(s.station_id)

    frames = []
    for source, ids in overrides.items():
        if source == "snotel":
            frames.append(_snotel_daily(ids, begin, end))
        elif source == "era5":
            frames.append(_era5_daily(ids, league, begin, end))
        else:
            raise ValueError(f"unknown truth source: {source!r}")
    if not frames:
        return pd.DataFrame(columns=["station_id", "date", "snow24", "valid", "qc_flag"])
    return pd.concat(frames, ignore_index=True)

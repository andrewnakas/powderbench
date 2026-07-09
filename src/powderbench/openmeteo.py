"""Client for Open-Meteo forecast APIs (NWP baselines).

Live: https://api.open-meteo.com/v1/forecast
Archive of past model runs (2021+): https://historical-forecast-api.open-meteo.com/v1/forecast
Daily snowfall_sum is in cm and aligned to station-local days (timezone=auto),
matching how SNOTEL truth days are defined.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .stations import Station, data_dir

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
CM_PER_INCH = 2.54
TIMEOUT = 120
BATCH_SIZE = 25

NWP_MODELS = ("best_match", "gfs_seamless")


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return data_dir() / "cache" / "openmeteo" / f"{digest}.json"


def _get(url: str, params: dict, cacheable: bool) -> list | dict:
    key = url + "?" + json.dumps(params, sort_keys=True)
    cache = _cache_path(key)
    if cacheable and cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(3):
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        if resp.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        resp.raise_for_status()
        payload = resp.json()
        if cacheable:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload))
        return payload
    resp.raise_for_status()
    return resp.json()


def _daily_frame(stations: list[Station], payload: list | dict) -> pd.DataFrame:
    results = payload if isinstance(payload, list) else [payload]
    if len(results) != len(stations):
        raise ValueError(f"Open-Meteo returned {len(results)} results for {len(stations)} stations")
    rows = []
    for station, res in zip(stations, results):
        daily = res["daily"]
        for day, cm in zip(daily["time"], daily["snowfall_sum"]):
            rows.append(
                {
                    "station_id": station.station_id,
                    "date": date.fromisoformat(day),
                    "snowfall_in": None if cm is None else cm / CM_PER_INCH,
                }
            )
    return pd.DataFrame(rows)


def _fetch(url: str, stations: list[Station], model: str, begin: date, end: date, cacheable: bool) -> pd.DataFrame:
    frames = []
    for i in range(0, len(stations), BATCH_SIZE):
        batch = stations[i : i + BATCH_SIZE]
        params = {
            "latitude": ",".join(f"{s.latitude:.5f}" for s in batch),
            "longitude": ",".join(f"{s.longitude:.5f}" for s in batch),
            "elevation": ",".join(f"{s.elevation_ft * 0.3048:.0f}" for s in batch),
            "daily": "snowfall_sum",
            "timezone": "auto",
            "start_date": begin.isoformat(),
            "end_date": end.isoformat(),
        }
        if model != "best_match":
            params["models"] = model
        frames.append(_daily_frame(batch, _get(url, params, cacheable)))
    return pd.concat(frames, ignore_index=True)


def forecast_daily_snowfall(stations: list[Station], begin: date, end: date, model: str = "best_match") -> pd.DataFrame:
    """Live forecast: daily snowfall (inches) per station-local day in [begin, end]."""
    return _fetch(FORECAST_URL, stations, model, begin, end, cacheable=False)


def hindcast_daily_snowfall(stations: list[Station], begin: date, end: date, model: str = "best_match") -> pd.DataFrame:
    """Archived past forecasts (what the model actually predicted at the time,
    short lead). Available from ~2021-03. Cached on disk."""
    return _fetch(HISTORICAL_URL, stations, model, begin, end, cacheable=True)

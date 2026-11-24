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
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .stations import Station, data_dir

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CM_PER_INCH = 2.54
TIMEOUT = 120
BATCH_SIZE = 25
# free tier: ~600 location-calls/min (a 25-location request counts 25), so
# space uncached requests to stay under it when a league has thousands of stations
MIN_INTERVAL_S = BATCH_SIZE * 60 / 600 + 0.1
_last_request = [0.0]

NWP_MODELS = ("best_match", "gfs_seamless")


class OpenMeteoDisabled(RuntimeError):
    """PB_OPENMETEO=0: run without Open-Meteo (cached responses still serve)."""


def enabled() -> bool:
    return os.environ.get("PB_OPENMETEO", "1") != "0"

# One-off jobs from a single IP (hindcast gates, climatology builds on a home
# box) can outrun the free tier's hourly (5,000) or daily (10,000) call limit.
# With PB_OPENMETEO_PATIENT=1 a 429 that names those windows waits for the
# window to reset instead of failing after the per-minute retries. Scheduled
# runs keep the default: a baseline that cannot be fetched in time is skipped.
# The daily count does not reset at 00:00 UTC for us: on 2026-11-20 the home
# IP was refused "Daily ... try again tomorrow" at 02:48 UTC, so the daily case
# probes again hourly (one call) rather than sleeping until midnight.
PATIENT = os.environ.get("PB_OPENMETEO_PATIENT") == "1"
MAX_PATIENT_WAITS = 48
DAILY_PROBE_S = 3600.0


def _window_wait(resp: requests.Response | None, now: datetime | None = None) -> float | None:
    """Seconds until the hourly / daily window named by a 429 resets, else None."""
    try:
        reason = str(resp.json().get("reason", "")) if resp is not None else ""
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    if "Hourly" in reason:
        return float((59 - now.minute) * 60 + (60 - now.second) + 30)
    if "Daily" in reason:
        midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
        return min((midnight - now).total_seconds() + 60, DAILY_PROBE_S)
    return None


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return data_dir() / "cache" / "openmeteo" / f"{digest}.json"


def _get(url: str, params: dict, cacheable: bool) -> list | dict:
    key = url + "?" + json.dumps(params, sort_keys=True)
    cache = _cache_path(key)
    if cacheable and cache.exists():
        return json.loads(cache.read_text())
    if not enabled():
        raise OpenMeteoDisabled(f"Open-Meteo disabled (PB_OPENMETEO=0): {url}")
    attempt = patient_waits = 0
    while True:
        wait_for = _last_request[0] + MIN_INTERVAL_S - time.monotonic()
        if wait_for > 0:
            time.sleep(wait_for)
        _last_request[0] = time.monotonic()
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(f"{resp.status_code}", response=resp)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:  # incl. dropped/truncated responses
            r = getattr(exc, "response", None)
            limited = getattr(r, "status_code", None) == 429
            window = _window_wait(r) if limited and PATIENT else None
            if window is not None and patient_waits < MAX_PATIENT_WAITS:
                patient_waits += 1
                log.warning("Open-Meteo %s; waiting %.0f s for the window to reset",
                            r.json().get("reason"), window)
                time.sleep(window)
                continue
            if attempt == 4:
                raise
            wait = 60 if limited else 10 * 2**attempt  # 429 = the per-minute window
            log.warning("Open-Meteo request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
            attempt += 1
    payload = resp.json()
    if cacheable:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    return payload


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
                    # ECMWF's de-accumulated snowfall can come back a hair below
                    # zero (-0.07 cm); a negative row voids the whole submission
                    "snowfall_in": None if cm is None else max(cm, 0.0) / CM_PER_INCH,
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


def era5_daily_snowfall(stations: list[Station], begin: date, end: date) -> pd.DataFrame:
    """ERA5 reanalysis daily snowfall (inches) per station-local day.
    Lags realtime by ~5 days; complete back to 1940. Cached when old enough
    that the archive can no longer change (end 10+ days ago).

    Pinned to era5_seamless (pure reanalysis): the archive's default
    best_match blends in the same high-res operational data the forecast
    archive serves, which would make truth identical to the NWP baseline
    (verified: MAE would be exactly 0)."""
    cacheable = end <= date.today() - timedelta(days=10)
    return _fetch(ARCHIVE_URL, stations, "era5_seamless", begin, end, cacheable=cacheable)

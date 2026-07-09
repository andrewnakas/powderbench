"""Client for the NRCS AWDB REST API (SNOTEL ground truth).

Docs: https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html
Daily element values are the 00:00 station-local readings. SNWD = snow depth
(inches), WTEQ = snow water equivalent (inches).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .stations import data_dir

log = logging.getLogger(__name__)

BASE_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
ELEMENTS = ("SNWD", "WTEQ")
BATCH_SIZE = 20
TIMEOUT = 60


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return data_dir() / "cache" / "snotel" / f"{digest}.json"


def _get(url: str, params: dict, cacheable: bool) -> list:
    key = url + "?" + json.dumps(params, sort_keys=True)
    cache = _cache_path(key)
    if cacheable and cache.exists():
        return json.loads(cache.read_text())
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if cacheable:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    return payload


def fetch_daily(
    station_ids: list[str],
    begin: date,
    end: date,
    elements: tuple[str, ...] = ELEMENTS,
) -> pd.DataFrame:
    """Fetch daily element values. Returns tidy frame:
    station_id, date, <one column per element>. Missing readings are NaN.

    Responses are cached on disk only when the window ended 3+ days ago,
    so recent (still-mutable) data is always re-fetched.
    """
    cacheable = end <= date.today() - timedelta(days=3)
    frames = []
    for i in range(0, len(station_ids), BATCH_SIZE):
        batch = station_ids[i : i + BATCH_SIZE]
        payload = _get(
            f"{BASE_URL}/data",
            {
                "stationTriplets": ",".join(batch),
                "elements": ",".join(elements),
                "duration": "DAILY",
                "beginDate": begin.isoformat(),
                "endDate": end.isoformat(),
                "periodRef": "END",
                "returnFlags": "false",
            },
            cacheable,
        )
        for station in payload:
            sid = station["stationTriplet"]
            for series in station.get("data", []):
                element = series["stationElement"]["elementCode"]
                for v in series.get("values", []):
                    frames.append(
                        {
                            "station_id": sid,
                            "date": v["date"],
                            "element": element,
                            "value": v.get("value"),
                        }
                    )
    if not frames:
        return pd.DataFrame(columns=["station_id", "date", *elements])
    df = pd.DataFrame(frames)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    wide = df.pivot_table(
        index=["station_id", "date"], columns="element", values="value", aggfunc="first"
    ).reset_index()
    wide.columns.name = None
    for el in elements:
        if el not in wide.columns:
            wide[el] = float("nan")
    return wide[["station_id", "date", *elements]]


def fetch_station_metadata(station_ids: list[str]) -> list[dict]:
    return _get(
        f"{BASE_URL}/stations",
        {"stationTriplets": ",".join(station_ids), "activeOnly": "true"},
        cacheable=False,
    )

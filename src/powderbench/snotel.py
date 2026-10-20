"""Client for the NRCS AWDB REST API (SNOTEL ground truth).

Docs: https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html
Daily element values are the 00:00 station-local readings. SNWD = snow depth
(inches), WTEQ = snow water equivalent (inches).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .stations import data_dir

log = logging.getLogger(__name__)

BASE_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
ELEMENTS = ("SNWD", "WTEQ")
BATCH_SIZE = 20
TIMEOUT = 120
RETRIES = 7  # these federal APIs throw intermittent 500s and stalls
CHUNK_DAYS = 366
WORKERS = 4  # concurrent requests for long multi-station histories  # long histories are fetched (and cached) a year at a time


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return data_dir() / "cache" / "snotel" / f"{digest}.json"


def _get(url: str, params: dict, cacheable: bool) -> list:
    key = url + "?" + json.dumps(params, sort_keys=True)
    cache = _cache_path(key)
    if cacheable and cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(f"{resp.status_code}", response=resp)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:  # incl. dropped/truncated responses
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("SNOTEL request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    payload = resp.json()
    if cacheable:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    return payload


def _chunks(begin: date, end: date) -> list[tuple[date, date]]:
    """Calendar-year-aligned chunks, so cache keys stay stable as `end` moves."""
    if (end - begin).days < CHUNK_DAYS:
        return [(begin, end)]
    out, cur = [], begin
    while cur <= end:
        stop = min(date(cur.year, 12, 31), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


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
    jobs = []
    for c_begin, c_end in _chunks(begin, end):
        cacheable = c_end <= date.today() - timedelta(days=3)
        for i in range(0, len(station_ids), BATCH_SIZE):
            batch = station_ids[i : i + BATCH_SIZE]
            jobs.append(({
                "stationTriplets": ",".join(batch),
                "elements": ",".join(elements),
                "duration": "DAILY",
                "beginDate": c_begin.isoformat(),
                "endDate": c_end.isoformat(),
                "periodRef": "END",
                "returnFlags": "false",
            }, cacheable))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        payloads = list(pool.map(lambda job: _get(f"{BASE_URL}/data", *job), jobs))
    sids, dates, els, vals = [], [], [], []
    for payload in payloads:
        for station in payload:
            sid = station["stationTriplet"]
            for series in station.get("data", []):
                element = series["stationElement"]["elementCode"]
                for v in series.get("values", []):
                    sids.append(sid)
                    dates.append(v["date"])
                    els.append(element)
                    vals.append(v.get("value"))
    if not sids:
        return pd.DataFrame(columns=["station_id", "date", *elements])
    df = pd.DataFrame({"station_id": sids, "date": dates, "element": els, "value": vals})
    del sids, dates, els, vals
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

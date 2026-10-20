"""Environment and Climate Change Canada daily climate data — measured new
snowfall (TOTAL_SNOW, cm) from staffed climate stations (Whistler Roundhouse,
Silver Star, Grouse Mountain, Kananaskis, Barkerville, ...). Open Government
Licence – Canada. Keyless OGC API: api.weather.gc.ca/collections/climate-daily.

Station ids: '<CLIMATE_IDENTIFIER>:<PROV>:ECCC'. Day attribution is fitted per
station (most report same-day; see networks/attribution.py).

QC (voided for everyone): no value, flag M (missing), A (accumulated over
several days — the day can't be isolated), E (estimated); trace (T) = 0;
negative or > 48".
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

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

URL = "https://api.weather.gc.ca/collections/climate-daily/items"
CM_PER_IN = 2.54
TIMEOUT = 120
RETRIES = 7
CHUNK_DAYS = 366
WORKERS = 4
DEFAULT_OFFSET = 0
VOID_FLAGS = {"M", "A", "E"}
QC_SOURCE_QC = "source_qc_failed"


def climate_id(station_id: str) -> str:
    return station_id.split(":")[0]


def _cache_path(key: str) -> Path:
    return data_dir() / "cache" / "eccc" / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"


def _get(params: dict, cacheable: bool) -> list[dict]:
    key = json.dumps(params, sort_keys=True)
    cache = _cache_path(key)
    if cacheable and cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(RETRIES):
        try:
            resp = requests.get(URL, params=params, timeout=TIMEOUT)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(str(resp.status_code), response=resp)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:  # incl. dropped/truncated responses
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("ECCC request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    feats = [f["properties"] for f in resp.json().get("features", [])]
    if cacheable:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(feats))
    return feats


def _chunks(begin: date, end: date) -> list[tuple[date, date]]:
    if (end - begin).days < CHUNK_DAYS:
        return [(begin, end)]
    out, cur = [], begin
    while cur <= end:
        stop = min(date(cur.year, 12, 31), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def fetch_snow(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Raw TOTAL_SNOW reports (inches) dated [begin, end]: station_id, date, snow_in, flag."""
    jobs = []
    for sid in station_ids:
        for c_begin, c_end in _chunks(begin, end):
            jobs.append((sid, {
                "f": "json", "limit": 1000, "CLIMATE_IDENTIFIER": climate_id(sid),
                "datetime": f"{c_begin.isoformat()}/{c_end.isoformat()}",
                "properties": "LOCAL_DATE,TOTAL_SNOW,TOTAL_SNOW_FLAG",
            }, c_end <= date.today() - timedelta(days=45)))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda j: (j[0], _get(j[1], j[2])), jobs))
    rows = []
    for sid, feats in results:
        for p in feats:
            val = p.get("TOTAL_SNOW")
            flag = (p.get("TOTAL_SNOW_FLAG") or "").strip().upper()
            if val is None and flag != "T":
                continue
            rows.append({"station_id": sid, "date": date.fromisoformat(p["LOCAL_DATE"][:10]),
                         "snow_in": 0.0 if flag == "T" else float(val) / CM_PER_IN, "flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow_in", "flag"])


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end]: day D <- the report dated D+offset."""
    offsets = offsets or {}
    lo = min([DEFAULT_OFFSET, *offsets.values()])
    hi = max([DEFAULT_OFFSET, *offsets.values()])
    raw = fetch_snow(station_ids, begin + timedelta(days=lo), end + timedelta(days=hi))
    have = {(r.station_id, r.date - timedelta(days=offsets.get(r.station_id, DEFAULT_OFFSET))): (r.snow_in, r.flag)
            for r in raw.itertuples()}
    rows = []
    for sid in station_ids:
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            rec = have.get((sid, d))
            flag, snow = truth.QC_OK, float("nan")
            if rec is None:
                flag = truth.QC_MISSING
            elif rec[1] in VOID_FLAGS:
                flag = QC_SOURCE_QC
            elif rec[0] < 0 or rec[0] > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            else:
                snow = rec[0]
            rows.append({"station_id": sid, "date": d, "snow24": snow,
                         "valid": flag == truth.QC_OK, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

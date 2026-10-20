"""NOAA GHCN-Daily measured new snowfall (element SNOW) — the human-observer
networks: NWS COOP and CoCoRaHS. Public domain (CoCoRaHS data: CC BY 3.0,
attributed in docs/DATA.md).

Unlike SNOTEL (snow24 derived from depth deltas), SNOW is *measured* new snow
on a snowboard, so this is the truth for the `observers` league.

Observation-day alignment: COOP and CoCoRaHS observers read the board in the
morning (~07:00 local), and GHCN files the reading under the observation
date. So the GHCN value dated D+1 is the snow that fell over local day D —
we score day D against the D+1 report. (A minority of COOP sites observe in
the afternoon; for them this attribution is ~half a day early. That's a known,
documented caveat and is the same for every competitor.)

Access: NCEI Access Data Service (keyless, JSON). Latency: CoCoRaHS lands in
~1 day, COOP keyed forms can take longer; the league resolves at D+6.
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

URL = "https://www.ncei.noaa.gov/access/services/data/v1"
BATCH_SIZE = 50
TIMEOUT = 120
RETRIES = 7  # these federal APIs throw intermittent 500s and stalls
CHUNK_DAYS = 366
WORKERS = 4
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}

# GHCN measurement flags: T = trace (record 0); quality flags: any non-blank
# quality flag means NCEI's own QC failed the value -> void.


def _cache_path(key: str) -> Path:
    return data_dir() / "cache" / "ghcnd" / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"


def _get(params: dict, cacheable: bool) -> list:
    key = json.dumps(params, sort_keys=True)
    cache = _cache_path(key)
    if cacheable and cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(RETRIES):
        try:
            resp = requests.get(URL, params=params, timeout=TIMEOUT, headers=UA)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(str(resp.status_code), response=resp)
            resp.raise_for_status()
            break
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("GHCN-D request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    payload = resp.json() if resp.content.strip() else []
    if cacheable:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    return payload


def _chunks(begin: date, end: date) -> list[tuple[date, date]]:
    if (end - begin).days < CHUNK_DAYS:
        return [(begin, end)]
    out, cur = [], begin
    while cur <= end:
        stop = min(date(cur.year, 12, 31), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def ghcn_id(station_id: str) -> str:
    """PowderBench id '<GHCNID>:<ST>:GHCND' -> GHCN id."""
    return station_id.split(":")[0]


def fetch_snow(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Raw GHCN SNOW reports (inches) filed under [begin, end]:
    station_id, date, snow_in, qflag."""
    by_ghcn = {ghcn_id(s): s for s in station_ids}
    ghcn = sorted(by_ghcn)
    jobs = []
    for c_begin, c_end in _chunks(begin, end):
        cacheable = c_end <= date.today() - timedelta(days=45)  # late COOP forms
        for i in range(0, len(ghcn), BATCH_SIZE):
            jobs.append(({
                "dataset": "daily-summaries",
                "stations": ",".join(ghcn[i : i + BATCH_SIZE]),
                "dataTypes": "SNOW",
                "startDate": c_begin.isoformat(),
                "endDate": c_end.isoformat(),
                "format": "json",
                "units": "standard",
                "includeAttributes": "true",
            }, cacheable))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        payloads = list(pool.map(lambda job: _get(*job), jobs))
    rows = []
    for payload in payloads:
        for rec in payload:
            if rec.get("SNOW") in (None, ""):
                continue
            attrs = (rec.get("SNOW_ATTRIBUTES") or ",,,").split(",")
            mflag = attrs[0] if attrs else ""
            qflag = attrs[1] if len(attrs) > 1 else ""
            val = 0.0 if mflag == "T" else float(rec["SNOW"])
            rows.append({"station_id": by_ghcn.get(rec["STATION"], rec["STATION"]),
                         "date": date.fromisoformat(rec["DATE"]), "snow_in": val, "qflag": qflag.strip()})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow_in", "qflag"])


def daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Truth frame for local days [begin, end]: day D <- the report filed D+1."""
    raw = fetch_snow(station_ids, begin + timedelta(days=1), end + timedelta(days=1))
    have = {(r.station_id, r.date - timedelta(days=1)): (r.snow_in, r.qflag) for r in raw.itertuples()}
    rows = []
    for sid in station_ids:
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            rec = have.get((sid, d))
            flag = truth.QC_OK
            snow = float("nan")
            if rec is None:
                flag = truth.QC_MISSING
            elif rec[1]:
                flag = QC_SOURCE_QC
            elif rec[0] < 0 or rec[0] > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            else:
                snow = rec[0]
            rows.append({"station_id": sid, "date": d, "snow24": snow,
                         "valid": flag == truth.QC_OK, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])


QC_SOURCE_QC = "source_qc_failed"

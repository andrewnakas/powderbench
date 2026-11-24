"""Environment and Climate Change Canada: daily snow on the ground
(SNOW_ON_GROUND, cm) at climate stations in the Canadian mountains (Banff,
Golden, Nordegg, Macmillan Pass...), for the world league. Open Government
Licence - Canada. Keyless OGC API: api.weather.gc.ca/collections/climate-daily
(the same one networks/eccc.py reads for measured new snow).

The value dated D is read in the morning of D: a storm on D shows in D+1's
depth (Golden, 2025-12-24: 26.8 mm of precipitation, 17 -> 39 cm the next
morning). So day D's new snow is max(depth(D+k) - depth(D+k-1), 0),
k = day_offset (default 1), counted from MIN_RISE_CM (the automatic sensors
jitter by 1 cm on dry days).

Station ids: '<CLIMATE_IDENTIFIER>:<PROV>:ECCCSD'.

QC (voided for everyone): either reading missing or flagged M/E; a depth
below 0 or above the shared ceiling; a rise above the shared 24 h limit; a
rise of DRIFT_RISE_CM or more on a day the station's own gauge recorded under
DRY_MM (the day's TOTAL_PRECIPITATION, between the two readings; no check
when the gauge has no value). Trace (T) reads 0.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from .. import truth
from . import eccc

SUFFIX = "ECCCSD"
DEFAULT_OFFSET = 1
MIN_RISE_CM = 2
DRIFT_RISE_CM = 10
DRY_MM = 0.5
VOID_FLAGS = {"M", "E"}
IN_PER_CM = 1 / 2.54


def station_id(climate_id: str, prov: str) -> str:
    return f"{climate_id}:{prov}:{SUFFIX}"


def parse(feats: list[dict]) -> dict[date, tuple[float, float]]:
    """climate-daily features -> {date: (depth cm or NaN, precipitation mm or NaN)}."""
    out = {}
    for p in feats:
        d = date.fromisoformat(p["LOCAL_DATE"][:10])
        dep, flag = p.get("SNOW_ON_GROUND"), (p.get("SNOW_ON_GROUND_FLAG") or "").strip().upper()
        if flag == "T":
            dep = 0.0
        elif dep is None or flag in VOID_FLAGS:
            dep = math.nan
        pr = p.get("TOTAL_PRECIPITATION")
        pr_flag = (p.get("TOTAL_PRECIPITATION_FLAG") or "").strip().upper()
        if pr_flag == "T":
            pr = 0.0
        elif pr is None or pr_flag in VOID_FLAGS:
            pr = math.nan
        out[d] = (float(dep), float(pr))
    return out


def fetch(station_ids: list[str], begin: date, end: date) -> dict[str, dict[date, tuple[float, float]]]:
    jobs = []
    for sid in station_ids:
        for c_begin, c_end in eccc._chunks(begin, end):
            jobs.append((sid, {
                "f": "json", "limit": 1000, "CLIMATE_IDENTIFIER": eccc.climate_id(sid),
                "datetime": f"{c_begin.isoformat()}/{c_end.isoformat()}",
                "properties": "LOCAL_DATE,SNOW_ON_GROUND,SNOW_ON_GROUND_FLAG,"
                              "TOTAL_PRECIPITATION,TOTAL_PRECIPITATION_FLAG",
            }, c_end <= date.today() - timedelta(days=45)))
    with ThreadPoolExecutor(max_workers=eccc.WORKERS) as pool:
        results = list(pool.map(lambda j: (j[0], eccc._get(j[1], j[2])), jobs))
    out: dict[str, dict] = {sid: {} for sid in station_ids}
    for sid, feats in results:
        out[sid].update(parse(feats))
    return out


def daily_from(series: dict[date, tuple[float, float]], sid: str, begin: date, end: date, k: int) -> list[dict]:
    rows = []
    nan = (math.nan, math.nan)
    for i in range((end - begin).days + 1):
        d = begin + timedelta(days=i)
        (s0, p), (s1, _) = series.get(d + timedelta(days=k - 1), nan), series.get(d + timedelta(days=k), nan)
        flag = truth.QC_OK
        if math.isnan(s0) or math.isnan(s1):
            flag = truth.QC_MISSING
        elif s0 < 0 or s1 < 0 or s1 * IN_PER_CM > truth.MAX_DEPTH_IN:
            flag = truth.QC_BAD_DEPTH
        elif (s1 - s0) * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
            flag = truth.QC_IMPLAUSIBLE
        elif s1 - s0 >= DRIFT_RISE_CM and not math.isnan(p) and p < DRY_MM:
            flag = truth.QC_NO_SWE_SUPPORT
        valid = flag == truth.QC_OK
        rise = s1 - s0 if valid else 0.0
        snow = round(rise * IN_PER_CM, 3) if valid and rise >= MIN_RISE_CM else (0.0 if valid else math.nan)
        rows.append({"station_id": sid, "date": d, "snow24": snow, "valid": valid, "qc_flag": flag})
    return rows


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from morning depth rises."""
    offsets = offsets or {}
    ks = [offsets.get(s, DEFAULT_OFFSET) for s in station_ids] or [DEFAULT_OFFSET]
    raw = fetch(station_ids, begin + timedelta(days=min(ks) - 1), end + timedelta(days=max(ks)))
    rows = []
    for sid in station_ids:
        rows += daily_from(raw.get(sid, {}), sid, begin, end, offsets.get(sid, DEFAULT_OFFSET))
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

"""California DWR CDEC snow pillows (Sierra Nevada, Southern Cascades).

CDEC's *daily* snow-depth value is not the midnight reading (during storms it
runs above the 00:00 hourly value — a daily mean), and differencing daily
means smears a storm across two days. So we read HOURLY depth (sensor 18)
and SWE (sensor 3) and take each local day's END value as the median of the
23:00, 00:00 and 01:00 readings around local midnight (CDEC reports in PST
year-round) — robust to the single-reading noise these ultrasonic sensors
show. Then the same QC as SNOTEL (truth.daily_snowfall: a >=6" jump needs an
SWE rise) plus a density check: any reading with depth < SWE is physically
impossible and is dropped before differencing.

Station ids: '<CDEC id>:CA:CDEC'. Public data, California DWR.
"""

from __future__ import annotations

import hashlib
import io
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

URL = "https://cdec.water.ca.gov/dynamicapp/req/CSVDataServlet"
SENSORS = {"18": "SNWD", "3": "WTEQ"}
RETRIES = 7
WORKERS = 4
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
# hourly history is one request per station-month: keep it to recent winters
HISTORY_START = date(2021, 10, 1)


def cdec_id(station_id: str) -> str:
    return station_id.split(":")[0]


def _months(begin: date, end: date) -> list[tuple[date, date]]:
    out, cur = [], date(begin.year, begin.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((cur, nxt - timedelta(days=1)))
        cur = nxt
    return out


def _cache(key: str) -> Path:
    return data_dir() / "cache" / "cdec" / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.csv"


def _month(station: str, m_begin: date, m_end: date) -> pd.DataFrame:
    """Hourly SNWD/WTEQ for one station-month (cached once the month is old)."""
    key = json.dumps([station, m_begin.isoformat()])
    cache = _cache(key)
    cacheable = m_end <= date.today() - timedelta(days=5)
    if cacheable and cache.exists():
        return pd.read_csv(cache)
    params = {"Stations": station, "SensorNums": ",".join(SENSORS), "dur_code": "H",
              "Start": m_begin.isoformat(), "End": (m_end + timedelta(days=1)).isoformat()}
    for attempt in range(RETRIES):
        try:
            r = requests.get(URL, params=params, timeout=180, headers=UA)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            raw = pd.read_csv(io.StringIO(r.text))
            break
        except (requests.RequestException, pd.errors.ParserError) as exc:
            if attempt == RETRIES - 1:
                raise
            time.sleep(min(5 * 2**attempt, 60))
            log.warning("CDEC %s %s failed (%s), retrying", station, m_begin, exc)
    if len(raw):
        raw = raw.rename(columns=str.strip)
        raw = raw[["SENSOR_NUMBER", "DATE TIME", "VALUE"]].rename(
            columns={"SENSOR_NUMBER": "sensor", "DATE TIME": "time", "VALUE": "value"})
    else:
        raw = pd.DataFrame(columns=["sensor", "time", "value"])
    if cacheable:
        cache.parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(cache, index=False)
    return raw


def fetch_daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """station_id, date, SNWD, WTEQ — end-of-local-day values (inches)."""
    begin = max(begin, HISTORY_START)
    if begin > end:
        return pd.DataFrame(columns=["station_id", "date", "SNWD", "WTEQ"])
    jobs = [(sid, a, b) for sid in station_ids for a, b in _months(begin, end + timedelta(days=1))]
    with ThreadPoolExecutor(WORKERS) as pool:
        parts = list(pool.map(lambda j: (j[0], _month(cdec_id(j[0]), j[1], j[2])), jobs))
    rows = []
    for sid, raw in parts:
        if not len(raw):
            continue
        df = raw.copy()
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df[(df["value"] > -9000) & df["value"].notna()]
        df["time"] = pd.to_datetime(df["time"], format="%Y%m%d %H%M", errors="coerce")
        df = df.dropna(subset=["time"])
        hr = df["time"].dt.hour
        df = df[hr.isin([23, 0, 1])]
        # 23:00 of day d and 00:00/01:00 of d+1 all describe the end of day d
        df["date"] = (df["time"] - pd.Timedelta(hours=2)).dt.date
        for sensor, name in SENSORS.items():
            sub = df[df["sensor"].astype(str) == sensor]
            med = sub.groupby("date")["value"].median()
            for d, v in med.items():
                rows.append({"station_id": sid, "date": d, "element": name, "value": float(v)})
    if not rows:
        return pd.DataFrame(columns=["station_id", "date", "SNWD", "WTEQ"])
    out = pd.DataFrame(rows).pivot_table(index=["station_id", "date"], columns="element",
                                         values="value", aggfunc="first").reset_index()
    out.columns.name = None
    for c in ("SNWD", "WTEQ"):
        if c not in out:
            out[c] = float("nan")
    # density check: snow can't hold more water than its own depth
    bad = out["SNWD"].notna() & out["WTEQ"].notna() & (out["SNWD"] < out["WTEQ"])
    out.loc[bad, "SNWD"] = float("nan")
    out = out.sort_values(["station_id", "date"]).reset_index(drop=True)
    for _, idx in out.groupby("station_id").groups.items():
        out.loc[idx, "SNWD"] = _sensor_qc(out.loc[idx, ["SNWD", "WTEQ"]].reset_index(drop=True)).to_numpy()
    return out[["station_id", "date", "SNWD", "WTEQ"]]


SPIKE_IN = 10.0  # depth this far above BOTH neighbouring days = a spike
STUCK_DAYS = 3
STUCK_SWE_RISE_IN = 0.5


def _sensor_qc(g: pd.DataFrame) -> pd.Series:
    """Blank depth readings from stuck or spiking ultrasonic sensors (seen at
    Mammoth Pass / Dana Meadows: a 134" ceiling while SWE kept rising)."""
    depth = g["SNWD"].copy()
    prev, nxt = depth.shift(1), depth.shift(-1)
    spike = (depth - prev > SPIKE_IN) & (depth - nxt > SPIKE_IN)
    run_id = (depth != prev).cumsum()
    for _, idx in depth.groupby(run_id).groups.items():
        if len(idx) >= STUCK_DAYS and pd.notna(depth[idx[0]]):
            swe = g.loc[idx, "WTEQ"]
            if swe.max() - swe.min() >= STUCK_SWE_RISE_IN:
                depth[idx] = float("nan")
    depth[spike] = float("nan")
    return depth


def daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    obs = fetch_daily(station_ids, begin - timedelta(days=1), end)
    d = truth.daily_snowfall(obs)
    full = pd.MultiIndex.from_product([station_ids, [begin + timedelta(days=i) for i in range((end - begin).days + 1)]],
                                      names=["station_id", "date"]).to_frame(index=False)
    d = full.merge(d, on=["station_id", "date"], how="left")
    miss = d["qc_flag"].isna()
    d.loc[miss, ["valid", "qc_flag"]] = [False, truth.QC_MISSING]
    d["valid"] = d["valid"].astype(bool)
    return d[["station_id", "date", "snow24", "valid", "qc_flag"]]

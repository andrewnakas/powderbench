"""British Columbia automated snow weather stations (ASWS), read from the
province's own feed (Government of British Columbia, Environmental Monitoring
and Analysis Branch; Open Government Licence – British Columbia).

41 BC pillows already reach the stations league through NRCS AWDB (network
MSNT, ids '<id>:BC:MSNT', regular SNOTEL truth). This adapter reads the rest
straight from BC: hourly snow depth (SD, cm) and SWE (SW, mm), one column per
station ('1A01P Yellowhead Lake'), timestamps in UTC:

  SD.csv / SW.csv                    the current water year (from Oct 1)
  SD_Archive.csv / SW_Archive.csv    every earlier one (from 2003-10)

As for CDEC, each local day's END value is the median of the 23:00, 00:00 and
01:00 readings around local midnight, in local *standard* time all year as
SNOTEL's daily values are: UTC-8, or UTC-7 for the stations on Mountain time
(`utc_offset`). Then the CDEC QC: SNOTEL's (truth.daily_snowfall: a >=6"
jump needs an SWE rise), density (depth < SWE is impossible), stuck sensor
and spike.

AWDB's MSNT daily value for day d is not a midnight reading: it is this
feed's 16:00 UTC (08:00 PST) reading on d+1, at all 40 stations in both feeds
(docs/DATA.md). The direct feed keeps the league's local-midnight day.

Station ids: '<BC id>:BC:ASWS'.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir
from .cdec import _sensor_qc

log = logging.getLogger(__name__)

BASE_URL = "https://www.env.gov.bc.ca/wsd/data_searches/snow/asws/data"
FILES = {"SNWD": "SD", "WTEQ": "SW"}
TO_INCHES = {"SNWD": 1 / 2.54, "WTEQ": 1 / 25.4}  # cm, mm
MAX_SWE_MM = 4000.0  # BC's deepest pillows peak near 2,400 mm; the feed has 99,981 mm glitches
RETRIES = 6
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
NETWORK = "ASWS"

PACIFIC, MOUNTAIN = -8, -7  # standard-time UTC offsets, hours
# hours (UTC) that hold 23:00/00:00/01:00 local standard time at UTC-8 and UTC-7
KEEP_UTC_HOURS = ("06", "07", "08", "09")
# the archive changes once a year, when the past water year moves into it
ARCHIVE_RECHECK_S = 86400


def station_id(bc_id: str) -> str:
    return f"{bc_id}:BC:{NETWORK}"


def bc_id(sid: str) -> str:
    return sid.split(":")[0]


# Stations whose IANA zone is not America/Vancouver, by Open-Meteo's
# timezone=auto lookup (the same one that sets the NWP baselines' local day):
# America/Edmonton (East Kootenay, Golden, Kootenay NP), America/Creston
# (Kootenay Lake's east shore) and America/Dawson_Creek (Peace) all keep UTC-7
# as standard time. scripts/networks/bc_asws_direct.py re-checks this list.
MOUNTAIN_IDS = frozenset({
    "2A21P", "2A30P", "2A33P", "2A36P", "2C09Q", "2C14P", "2C17AP", "2C21P", "2C23P", "2C24P",  # Edmonton
    "2D05P", "2D10P",  # Creston
    "4A31P", "4A33P", "4A37P", "4A38P", "4A39P", "4A46P", "4A48P", "4C01P",  # Dawson_Creek
})
MOUNTAIN_ZONES = ("America/Edmonton", "America/Creston", "America/Dawson_Creek")


def utc_offset(bc: str) -> int:
    """Standard-time UTC offset (hours) of a BC station's local day."""
    return MOUNTAIN if bc in MOUNTAIN_IDS else PACIFIC


def _cache_dir() -> Path:
    return data_dir() / "cache" / "bcasws"


def reduce_csv(src) -> pd.DataFrame:
    """A BC wide hourly CSV -> the rows near local midnight only (UTC hours
    KEEP_UTC_HOURS), indexed by UTC time, one float column per BC id."""
    parts = []
    for ch in pd.read_csv(src, chunksize=20000, dtype=str):
        ch = ch[ch.iloc[:, 0].str[11:13].isin(KEEP_UTC_HOURS)]
        if not len(ch):
            continue
        vals = ch.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").astype("float32")
        vals.index = pd.to_datetime(ch.iloc[:, 0], format="%Y-%m-%d %H:%M")
        vals.columns = [str(c).split()[0] for c in vals.columns]
        parts.append(vals)
    if not parts:
        return pd.DataFrame(dtype="float32")
    out = pd.concat(parts)
    out.index.name = "time_utc"
    return out[~out.index.duplicated(keep="last")].sort_index()


def _download(name: str, dest: Path) -> None:
    url = f"{BASE_URL}/{name}.csv"
    for attempt in range(RETRIES):
        try:
            with requests.get(url, headers=UA, timeout=300, stream=True) as r:
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(str(r.status_code))
                r.raise_for_status()
                with dest.open("wb") as f:
                    for block in r.iter_content(1 << 20):
                        f.write(block)
            return
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            log.warning("BC ASWS %s failed (%s), retrying", name, exc)
            time.sleep(min(10 * 2**attempt, 120))


def _fetch_reduced(name: str) -> pd.DataFrame:
    """Download one BC file and keep the near-midnight rows (the archive is
    ~50 MB; only an eighth of it is ever read)."""
    tmp = _cache_dir() / f".{name}.download"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(name, tmp)
        return reduce_csv(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _read_cache(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=[0], dtype="float32")
    df.index.name = "time_utc"
    return df


@lru_cache(maxsize=None)
def _current(element: str) -> pd.DataFrame:
    """The current water year — always re-read (once per process)."""
    return _fetch_reduced(FILES[element])


@lru_cache(maxsize=None)
def _archive(element: str, needed_until: datetime) -> pd.DataFrame:
    """Every earlier water year, cached on disk (reduced). Re-downloaded when
    it stops short of `needed_until` (a water year rolled over), at most
    once a day."""
    path = _cache_dir() / f"{FILES[element]}_Archive.csv.gz"
    if path.exists():
        df = _read_cache(path)
        fresh = time.time() - path.stat().st_mtime < ARCHIVE_RECHECK_S
        if fresh or (len(df) and df.index.max() >= needed_until):
            return df
    df = _fetch_reduced(f"{FILES[element]}_Archive")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def hourly(element: str, begin_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    """Near-midnight hourly readings in [begin_utc, end_utc] (feed units)."""
    cur = _current(element)
    frames = [cur]
    if not len(cur) or begin_utc < cur.index.min():
        # the archive should reach the UTC day before the current file starts
        until = (cur.index.min() if len(cur) else pd.Timestamp(end_utc)).normalize() - pd.Timedelta(days=1)
        frames.insert(0, _archive(element, until.to_pydatetime()))
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.loc[begin_utc:end_utc]


def end_of_day(wide: pd.DataFrame, offsets: dict[str, int]) -> pd.DataFrame:
    """Hourly wide frame (UTC index, one column per BC id) -> each local day's
    end value: the median of the 23:00/00:00/01:00 local readings. 23:00 of
    day d and 00:00/01:00 of d+1 all describe the end of day d.
    -> bc_id, date, value."""
    out = []
    for off in sorted(set(offsets.values())):
        cols = [c for c, o in offsets.items() if o == off and c in wide.columns]
        if not cols:
            continue
        local = wide.index + pd.Timedelta(hours=off)
        keep = local.hour.isin([23, 0, 1])
        sub = wide.loc[keep, cols]
        day = (local[keep] - pd.Timedelta(hours=2)).normalize()
        med = sub.groupby(day).median()
        long = med.stack().rename("value").reset_index()
        long.columns = ["date", "bc_id", "value"]
        out.append(long)
    if not out:
        return pd.DataFrame(columns=["bc_id", "date", "value"])
    res = pd.concat(out, ignore_index=True)
    res["date"] = res["date"].dt.date
    return res[["bc_id", "date", "value"]]


def fetch_daily(station_ids: list[str], begin: date, end: date,
                offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """station_id, date, SNWD, WTEQ — end-of-local-day values (inches).
    offsets: {BC id: UTC offset} (default: utc_offset of each station)."""
    offsets = offsets or {bc_id(s): utc_offset(bc_id(s)) for s in station_ids}
    # day d ends between 23:00 local on d and 01:00 local on d+1: 06:00..09:00 UTC on d+1
    b = datetime(begin.year, begin.month, begin.day)
    e = datetime(end.year, end.month, end.day) + timedelta(days=1, hours=12)
    ids = {bc_id(s): s for s in station_ids}
    frames = []
    for element, name in FILES.items():
        wide = hourly(element, b, e)
        wide = wide[[c for c in wide.columns if c in ids]]
        if element == "WTEQ":
            wide = wide.where(wide <= MAX_SWE_MM)
        long = end_of_day(wide, {k: v for k, v in offsets.items() if k in ids})
        long["element"] = element
        long["value"] = long["value"].astype(float) * TO_INCHES[element]
        frames.append(long)
    cols = ["station_id", "date", "SNWD", "WTEQ"]
    long = pd.concat(frames, ignore_index=True)
    long = long[(long["date"] >= begin) & (long["date"] <= end)]
    if not len(long):
        return pd.DataFrame(columns=cols)
    out = long.pivot_table(index=["bc_id", "date"], columns="element", values="value", aggfunc="first").reset_index()
    out.columns.name = None
    out["station_id"] = out["bc_id"].map(ids)
    for c in ("SNWD", "WTEQ"):
        if c not in out:
            out[c] = float("nan")
    # density check: snow can't hold more water than its own depth
    bad = out["SNWD"].notna() & out["WTEQ"].notna() & (out["SNWD"] < out["WTEQ"])
    out.loc[bad, "SNWD"] = float("nan")
    out = out.sort_values(["station_id", "date"]).reset_index(drop=True)
    for _, idx in out.groupby("station_id").groups.items():
        out.loc[idx, "SNWD"] = _sensor_qc(out.loc[idx, ["SNWD", "WTEQ"]].reset_index(drop=True)).to_numpy()
    return out[cols]


def daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    obs = fetch_daily(station_ids, begin - timedelta(days=1), end)
    d = truth.daily_snowfall(obs)
    full = pd.MultiIndex.from_product([station_ids, [begin + timedelta(days=i) for i in range((end - begin).days + 1)]],
                                      names=["station_id", "date"]).to_frame(index=False)
    d = full.merge(d, on=["station_id", "date"], how="left")
    miss = d["qc_flag"].isna()
    d.loc[miss, ["valid", "qc_flag"]] = [False, truth.QC_MISSING]
    d["valid"] = d["valid"].astype(bool)
    return d[["station_id", "date", "snow24", "swe24", "valid", "qc_flag"]]

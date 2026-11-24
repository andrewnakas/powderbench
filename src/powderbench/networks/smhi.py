"""SMHI (Swedish Meteorological and Hydrological Institute) — daily snow depth.

Parameter 8, "Snödjup, momentanvärde, 1 gång/dygn, kl 06" (metres): the depth
read at 06 UTC (07:00 CET) every morning, mostly by observers. Open data, no
key, CC BY 4.0: credit "SMHI" (https://opendata.smhi.se).

New snow is derived from depth like SNOTEL: the reading at 06 UTC on D+1
minus the reading on D covers 07:00 D .. 07:00 D+1 local, so day D <-
max(depth(D+1) - depth(D), 0). Settling makes long storms read a little low
and melt days read 0; every team is scored on the same number.

Data: the corrected archive (to ~3 months ago) plus `latest-months` for the
recent days. Both are cached per station per UTC day.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

URL = "https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/8/station/{code}/period/{period}/data.csv"
IN_PER_M = 39.3701
RETRIES = 5
PAUSE_S = 0.3
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
PERIODS = ("corrected-archive", "latest-months")


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def parse_csv(text: str) -> pd.DataFrame:
    """date, depth_m from an SMHI metobs CSV (the data block starts at 'Datum;')."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Datum;")), None)
    if start is None:
        return pd.DataFrame(columns=["date", "depth_m"])
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), sep=";", usecols=[0, 1, 2, 3],
                     names=["date", "time", "depth_m", "quality"], header=0, dtype=str)
    df = df[df["time"].str.startswith("06")]
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["depth_m"] = pd.to_numeric(df["depth_m"], errors="coerce")
    return df.dropna(subset=["date"])[["date", "depth_m"]]


def _get(code_: str, period: str) -> str:
    for attempt in range(RETRIES):
        try:
            r = requests.get(URL.format(code=code_, period=period), headers=UA, timeout=180)
            if r.status_code == 404:
                return ""
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            r.encoding = "utf-8"
            time.sleep(PAUSE_S)
            return r.text
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("SMHI request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def depths(code_: str) -> pd.DataFrame:
    """Full 06 UTC depth record (date, depth_m), cached per UTC day."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "smhi" / code_
    path = root / f"{today:%Y%m%d}.csv"
    if path.exists():
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    frames = [parse_csv(_get(code_, p)) for p in PERIODS]
    df = pd.concat(frames, ignore_index=True).drop_duplicates("date", keep="last").sort_values("date")
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob("*.csv"):
        old.unlink()
    df.to_csv(path, index=False)
    return df


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from 06 UTC depth deltas."""
    rows = []
    offsets = offsets or {}
    for sid in station_ids:
        # day D <- the rise from D+k-1 to D+k, k = day_offset (default 1, the
        # 06 UTC readings either side of day D; 0 where the dated reading is
        # taken at the end of day D, as the 2026-11 gate found for some stations)
        k = offsets.get(sid, 1)
        rec = depths(code(sid)).set_index("date")["depth_m"]
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            s0, s1 = rec.get(d + timedelta(days=k - 1)), rec.get(d + timedelta(days=k))
            flag = truth.QC_OK
            if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
                flag = truth.QC_MISSING
            elif s0 < 0 or s1 < 0 or s1 * IN_PER_M > truth.MAX_DEPTH_IN:
                flag = truth.QC_BAD_DEPTH
            elif (s1 - s0) * IN_PER_M > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            snow = max((s1 - s0) * IN_PER_M, 0.0) if valid else float("nan")
            rows.append({"station_id": sid, "date": d, "snow24": round(snow, 3) if valid else snow,
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

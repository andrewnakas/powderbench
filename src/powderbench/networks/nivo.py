"""Météo-France nivo-meteorological network — measured new snow at French ski
resorts: `ssfrai` (metres), read by the resort's observer on the snow board at
the morning observation (~07-08 local). Licence Ouverte 2.0 (Etalab): credit
"Météo-France" (data.gouv.fr "Archive réseau nivo-météorologique").

Some stations also file a midday observation (perssfrai -50, ~5 h since the
morning). The morning board (perssfrai -99) is the 24 h reading and already
includes that snow: after a midday report of fresh snow, the next morning's
reading averages 0.20 m against a 0.07 m midday-to-morning depth gain, and is
at least the 24 h depth gain 89% of the time. So only the first morning
observation of each day is used (adding the midday reading would double
count). Day D <- the report dated D+offset, fitted per station.

Data: one CSV per year on Météo-France's open-data bucket (the current year is
regenerated daily, ~06:30 UTC). Past years are cached once; the current year
at most once per UTC day.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

URL = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/OBS/NIVOSE/nivo_{year}.csv.gz"
M_TO_IN = 39.3701
MORNING = -99  # perssfrai of the morning (24 h) observation; -50 = midday
MORNING_BEFORE_UTC = 12
RETRIES = 6
DEFAULT_OFFSET = 1
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def _download(year: int, dest) -> None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(URL.format(year=year), headers=UA, timeout=300)
            if r.status_code == 404:
                dest.write_bytes(b"")
                return
            r.raise_for_status()
            dest.write_bytes(r.content)
            return
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("Météo-France nivo download failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)


@lru_cache(maxsize=8)
def _year(year: int) -> pd.DataFrame:
    """station (WMO id as str), date, ssfrai_m: each day's first morning reading."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "nivo"
    root.mkdir(parents=True, exist_ok=True)
    tag = f"{year}" if year < today.year else f"{year}-{today:%Y%m%d}"
    path = root / f"nivo_{tag}.csv.gz"
    if not path.exists():
        for old in root.glob(f"nivo_{year}-*.csv.gz"):
            old.unlink()
        _download(year, path)
    if not path.stat().st_size:
        return pd.DataFrame(columns=["station", "date", "ssfrai_m"])
    # older years are comma-separated, the current one semicolon-separated
    df = pd.read_csv(path, sep=None, engine="python", usecols=["geo_id_wmo", "validity_time", "ssfrai", "perssfrai"])
    t = pd.to_datetime(df["validity_time"], utc=True)
    morning = (df["perssfrai"].fillna(MORNING) == MORNING) & (t.dt.hour < MORNING_BEFORE_UTC)
    df = df[morning].assign(t=t[morning]).sort_values("t")
    df["date"] = df["t"].dt.date
    df["station"] = df["geo_id_wmo"].astype(int).astype(str).str.zfill(5)
    df = df.drop_duplicates(["station", "date"], keep="first")
    return df[["station", "date"]].assign(ssfrai_m=pd.to_numeric(df["ssfrai"], errors="coerce"))


def fetch_snow(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Raw morning new-snow reports (inches) dated [begin, end]."""
    codes = {code(s): s for s in station_ids}
    df = pd.concat([_year(y) for y in range(begin.year, end.year + 1)], ignore_index=True)
    df = df[df["station"].isin(codes) & (df["date"] >= begin) & (df["date"] <= end)]
    return pd.DataFrame({"station_id": df["station"].map(codes), "date": df["date"],
                         "snow_in": df["ssfrai_m"] * M_TO_IN})


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end]: day D <- the report dated D+offset."""
    offsets = offsets or {}
    lo = min([DEFAULT_OFFSET, *offsets.values()])
    hi = max([DEFAULT_OFFSET, *offsets.values()])
    raw = fetch_snow(station_ids, begin + timedelta(days=lo), end + timedelta(days=hi))
    have = {(r.station_id, r.date - timedelta(days=offsets.get(r.station_id, DEFAULT_OFFSET))): r.snow_in
            for r in raw.itertuples()}
    rows = []
    for sid in station_ids:
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            snow = have.get((sid, d))
            flag = truth.QC_OK
            if snow is None or pd.isna(snow):
                flag = truth.QC_MISSING
            elif snow < 0 or snow > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": float(snow) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

"""WSL Institute for Snow and Avalanche Research SLF — Swiss new snow (HN_1D,
cm, the height of new snow over the last 24h). CC BY 4.0: credit "WSL Institute
for Snow and Avalanche Research SLF" and link www.slf.ch.

Two networks, one adapter (picked by the station-id suffix):
  - `:SLF`  manual study plots — an observer's board reading at ~07:00 local
           (measure_date 06:00 UTC), ~100 sites, records to the 1930s
  - `:IMIS` automatic IMIS stations — HN_1D derived by SLF from the snow-depth
           sensor, ~130 sites, records to the 1990s

Data: the daily-regenerated bulk CSVs on measurement-data.slf.ch (full
history; downloaded at most once per UTC day and cached), topped up with the
live measurement API for the last week (the bulk files can lag a few days).
Day attribution is fitted per station (networks/attribution.py).
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

BULK = {
    "SLF": "https://measurement-data.slf.ch/study-plot/data/all-stations.csv",
    "IMIS": "https://measurement-data.slf.ch/imis/data/daily_snow_values/daily_snow.csv",
}
API = "https://measurement-api.slf.ch/public/api"
CM_PER_IN = 2.54
RETRIES = 7
DEFAULT_OFFSET = 1  # study plots read at 07:00 local -> the board holds yesterday's snow
LIVE_DAYS = 7
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def network(station_id: str) -> str:
    return station_id.rsplit(":", 1)[-1]


def _get(url: str, **kw) -> requests.Response:
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=600, **kw)
            if r.status_code == 404:
                return r
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("SLF request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _download(url: str, dest) -> None:
    """Stream to disk, resuming with HTTP Range after a dropped connection —
    the archives are ~50-90 MB and flaky links rarely deliver them in one go."""
    part = dest.with_suffix(dest.suffix + ".part")
    part.unlink(missing_ok=True)
    for attempt in range(RETRIES * 2):
        have = part.stat().st_size if part.exists() else 0
        headers = dict(UA, **({"Range": f"bytes={have}-"} if have else {}))
        try:
            with requests.get(url, headers=headers, timeout=120, stream=True) as r:
                if r.status_code == 416:  # nothing left to fetch
                    break
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                with part.open(mode) as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            break
        except requests.RequestException as exc:
            wait = min(5 * 2**min(attempt, 4), 60)
            log.warning("SLF download interrupted (%s), resuming in %ss", exc, wait)
            time.sleep(wait)
    else:
        raise RuntimeError(f"could not download {url}")
    part.rename(dest)


@lru_cache(maxsize=2)
def _bulk(net: str) -> pd.DataFrame:
    """The network's full-history daily file, cached on disk per UTC day."""
    today = datetime.now(timezone.utc).date().isoformat()
    cache = data_dir() / "cache" / "slf" / f"{net}-{today}.csv.gz"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        for old in cache.parent.glob(f"{net}-*.csv.gz"):
            old.unlink()
        raw = cache.parent / f"{net}-{today}.raw.csv"
        _download(BULK[net], raw)
        df = pd.read_csv(raw, usecols=["station_code", "measure_date", "HN_1D"])
        df.to_csv(cache, index=False)
        raw.unlink()
    df = pd.read_csv(cache)
    df["date"] = pd.to_datetime(df["measure_date"], utc=True).dt.date
    return df[["station_code", "date", "HN_1D"]]


def _live(net: str, codes: list[str]) -> pd.DataFrame:
    rows = []
    if net == "IMIS":
        r = _get(f"{API}/imis/daily-snow", params={"period_in_days": LIVE_DAYS})
        if r.ok:
            rows = [x for x in r.json() if x["station_code"] in set(codes)]
    else:
        for c in codes:
            r = _get(f"{API}/study-plot/station/{c}/measurements", params={"period_in_days": LIVE_DAYS})
            if r.ok and isinstance(r.json(), list):
                rows += [dict(x, station_code=c) for x in r.json()]
    if not rows:
        return pd.DataFrame(columns=["station_code", "date", "HN_1D"])
    df = pd.DataFrame(rows)
    if "HN_1D" not in df:
        df["HN_1D"] = float("nan")
    df["date"] = pd.to_datetime(df["measure_date"], utc=True).dt.date
    return df[["station_code", "date", "HN_1D"]]


def fetch_snow(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Raw HN_1D reports (inches) dated [begin, end]: station_id, date, snow_in
    (NaN = present but no reading; negative sentinels are kept for QC)."""
    frames = []
    by_net: dict[str, dict[str, str]] = {}
    for sid in station_ids:
        by_net.setdefault(network(sid), {})[code(sid)] = sid
    for net, codes in by_net.items():
        if net not in BULK:
            continue
        parts = [_bulk(net)]
        if end >= date.today() - timedelta(days=LIVE_DAYS):
            parts.append(_live(net, list(codes)))  # live API wins: listed last
        df = pd.concat(parts, ignore_index=True)
        df = df[df["station_code"].isin(codes) & (df["date"] >= begin) & (df["date"] <= end)]
        df = df.drop_duplicates(["station_code", "date"], keep="last")
        frames.append(pd.DataFrame({"station_id": df["station_code"].map(codes), "date": df["date"],
                                    "snow_in": pd.to_numeric(df["HN_1D"], errors="coerce") / CM_PER_IN}))
    if not frames:
        return pd.DataFrame(columns=["station_id", "date", "snow_in"])
    return pd.concat(frames, ignore_index=True)


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

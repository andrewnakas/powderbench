"""GeoSphere Austria (formerly ZAMG) — measured 24h new snow at Austrian
climate stations: `shneu_manu`, the observer's hand measurement of new snow
(cm) at the 06 UTC morning observation, -1 = no snow. Dataset klima-v2-1d,
CC BY 4.0: credit "GeoSphere Austria" (https://data.hub.geosphere.at).

The reading at 06 UTC (07:00 CET) on date D covers the previous 24 h, so it
should hold day D-1's snow (day D <- the report dated D+1, DEFAULT_OFFSET),
but fitted against ERA5 some stations clearly file it the other way, so every
station's offset is fitted and near-ties are excluded (networks/attribution.py).

Data: the GeoSphere Data Hub station API (no key). Past calendar years are
cached per station; the current year is re-fetched at most once per UTC day.
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

API = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-1d"
PARAMS = "shneu_manu,shneu_manu_flag"
CM_PER_IN = 2.54
BATCH = 40
RETRIES = 6
DEFAULT_OFFSET = 1
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def _get(params: dict) -> pd.DataFrame:
    for attempt in range(RETRIES):
        try:
            r = requests.get(API, params=dict(params, parameters=PARAMS, output_format="csv"),
                             headers=UA, timeout=180)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            return pd.read_csv(io.StringIO(r.text))
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("GeoSphere request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _year(codes: list[str], year: int) -> pd.DataFrame:
    """station, date, shneu_manu, shneu_manu_flag for one calendar year."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "geosphere"
    tag = f"{year}" if year < today.year else f"{year}-{today:%Y%m%d}"
    paths = {c: root / c / f"{tag}.csv" for c in codes}
    missing = [c for c, p in paths.items() if not p.exists()]
    for i in range(0, len(missing), BATCH):
        part = missing[i : i + BATCH]
        end = min(date(year, 12, 31), today)
        df = _get({"station_ids": ",".join(part), "start": f"{year}-01-01", "end": end.isoformat()})
        df["station"] = df["station"].astype(str)
        for c in part:
            p = paths[c]
            p.parent.mkdir(parents=True, exist_ok=True)
            for old in p.parent.glob(f"{year}-*.csv"):  # yesterday's copy of the current year
                old.unlink()
            df[df["station"] == c].to_csv(p, index=False)
    frames = [pd.read_csv(p, dtype={"station": str}) for p in paths.values()]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not len(out):
        return pd.DataFrame(columns=["station", "date", "shneu_manu", "shneu_manu_flag"])
    out["date"] = pd.to_datetime(out["time"], utc=True).dt.date
    return out[["station", "date", "shneu_manu", "shneu_manu_flag"]]


def fetch_snow(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Raw new-snow reports (inches) dated [begin, end]: station_id, date,
    snow_in (NaN = no reading; -1 "no snow" -> 0)."""
    codes = {code(s): s for s in station_ids}
    frames = [_year(list(codes), y) for y in range(begin.year, end.year + 1)]
    df = pd.concat(frames, ignore_index=True)
    df = df[(df["date"] >= begin) & (df["date"] <= end)]
    hn = pd.to_numeric(df["shneu_manu"], errors="coerce")
    hn = hn.where(hn != -1, 0.0)
    return pd.DataFrame({"station_id": df["station"].map(codes), "date": df["date"], "snow_in": hn / CM_PER_IN})


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

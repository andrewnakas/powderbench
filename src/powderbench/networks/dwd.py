"""Deutscher Wetterdienst (DWD) — measured new snow at German stations.

`NSH_TAG` of DWD's daily precipitation-station dataset (`more_precip`): the
new-snow depth (cm) an observer measures at the morning reading. Open data,
no key; credit "Deutscher Wetterdienst" (https://opendata.dwd.de, CC BY 4.0).

The value dated D is the morning-of-D measurement, i.e. mostly day D-1's
snow. Every high station checked (85 of 85, winter 2025/26) correlates best
with the precipitation total DWD files under D-1. So day D <- the value dated
D+1 (DEFAULT_OFFSET 1). -999 is missing.

Data: the `recent` zip (~the last 500 days) and the `historical` zip (to
the end of last year) per station. The historical file is cached for good;
the recent one is re-fetched at most once per UTC day.
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/more_precip"
CM_PER_IN = 2.54
DEFAULT_OFFSET = 1
RETRIES = 5
PAUSE_S = 0.2
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def _get(url: str) -> bytes | None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=180)
            if r.status_code == 404:
                return None
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.content
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("DWD request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def parse_zip(blob: bytes) -> pd.DataFrame:
    """date, nsh_cm from one tageswerte_RR_*.zip."""
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in zf.namelist() if n.startswith("produkt"))
    df = pd.read_csv(zf.open(name), sep=";", encoding="latin1", skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    nsh = pd.to_numeric(df["NSH_TAG"], errors="coerce")
    return pd.DataFrame({"date": pd.to_datetime(df["MESS_DATUM"].astype(str), format="%Y%m%d").dt.date,
                         "nsh_cm": nsh.where(nsh >= 0)})


_HIST_LISTING: dict[str, str] = {}


def _historical_name(code_: str) -> str | None:
    if not _HIST_LISTING:
        html = _get(f"{BASE}/historical/") or b""
        for name in re.findall(rb'href="(tageswerte_RR_(\d{5})_[^"]+\.zip)"', html):
            _HIST_LISTING[name[1].decode()] = name[0].decode()
    return _HIST_LISTING.get(code_)


def record(code_: str) -> pd.Series:
    """nsh_cm by date: historical + recent, cached."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "dwd" / code_
    root.mkdir(parents=True, exist_ok=True)
    hist = root / "historical.csv"
    if not hist.exists():
        name = _historical_name(code_)
        blob = _get(f"{BASE}/historical/{name}") if name else None
        (parse_zip(blob) if blob else pd.DataFrame(columns=["date", "nsh_cm"])).to_csv(hist, index=False)
    recent = root / f"recent-{today:%Y%m%d}.csv"
    if not recent.exists():
        for old in root.glob("recent-*.csv"):
            old.unlink()
        blob = _get(f"{BASE}/recent/tageswerte_RR_{code_}_akt.zip")
        (parse_zip(blob) if blob else pd.DataFrame(columns=["date", "nsh_cm"])).to_csv(recent, index=False)
    df = pd.concat([pd.read_csv(hist), pd.read_csv(recent)], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.drop_duplicates("date", keep="last").set_index("date")["nsh_cm"]


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end]: day D <- the value dated D+offset."""
    offsets = offsets or {}
    rows = []
    for sid in station_ids:
        rec = record(code(sid))
        k = offsets.get(sid, DEFAULT_OFFSET)
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            cm = rec.get(d + timedelta(days=k))
            flag = truth.QC_OK
            if cm is None or pd.isna(cm):
                flag = truth.QC_MISSING
            elif cm / CM_PER_IN > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": round(cm / CM_PER_IN, 3) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

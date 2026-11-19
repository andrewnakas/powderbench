"""MeteoSwiss — new snow measured by hand at the manual precipitation stations.

`hns000d0` of MeteoSwiss's open data (ogd-nime, "Manual precipitation
stations"): the fresh snow (cm) an observer reads on the board at 06 UTC,
covering 06 UTC D .. 06 UTC D+1 and dated D by MeteoSwiss (checked against
the station's own morning depth, which rises from the value dated D to the
one dated D+1). So day D <- the value dated D (DEFAULT_OFFSET 0), unlike SLF,
which dates the reading by the morning it is taken. Open data, no key,
CC BY 4.0: credit "Source: MeteoSwiss" (https://opendata.meteoswiss.ch).

These are mostly village and valley sites below the SLF study plots
(Adelboden, Pontresina, Engelberg, Zermatt, Grindelwald...): mid-elevation
truth next to the ski areas.

Data: per station, `_d_historical.csv` (the record to the end of last year,
re-read once a year) and `_d_recent.csv` (this year, re-read at most once per
UTC day; MeteoSwiss asks for no high-frequency downloads of the same file).
Semicolon CSV, latin-1, `reference_timestamp` "DD.MM.YYYY 00:00".
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

BASE = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-nime"
CM_PER_IN = 2.54
DEFAULT_OFFSET = 0
RETRIES = 5
PAUSE_S = 1.0
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
            log.warning("MeteoSwiss request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def parse_csv(blob: bytes) -> pd.DataFrame:
    """date, hn_cm, hs_cm from one ogd-nime daily file."""
    df = pd.read_csv(io.BytesIO(blob), sep=";", encoding="latin1", dtype=str)
    out = pd.DataFrame({"date": pd.to_datetime(df["reference_timestamp"].str.slice(0, 10), format="%d.%m.%Y",
                                               errors="coerce").dt.date})
    for col, name in (("hns000d0", "hn_cm"), ("hto000d0", "hs_cm")):
        v = pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(float("nan"), index=df.index)
        out[name] = v.where(v >= 0)
    return out.dropna(subset=["date"])


def _url(abbr: str, period: str) -> str:
    a = abbr.lower()
    return f"{BASE}/{a}/ogd-nime_{a}_d_{period}.csv"


def record(abbr: str) -> pd.DataFrame:
    """date-indexed hn_cm, hs_cm: historical + recent, cached."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "meteoswiss" / abbr.lower()
    root.mkdir(parents=True, exist_ok=True)
    empty = pd.DataFrame(columns=["date", "hn_cm", "hs_cm"])
    hist = root / f"historical-{today.year}.csv"
    if not hist.exists():
        for old in root.glob("historical-*.csv"):
            old.unlink()
        blob = _get(_url(abbr, "historical"))
        (parse_csv(blob) if blob else empty).to_csv(hist, index=False)
    recent = root / f"recent-{today:%Y%m%d}.csv"
    if not recent.exists():
        for old in root.glob("recent-*.csv"):
            old.unlink()
        blob = _get(_url(abbr, "recent"))
        (parse_csv(blob) if blob else empty).to_csv(recent, index=False)
    df = pd.concat([pd.read_csv(hist), pd.read_csv(recent)], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.drop_duplicates("date", keep="last").set_index("date")[["hn_cm", "hs_cm"]]


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end]: day D <- the new snow dated D+offset."""
    offsets = offsets or {}
    rows = []
    for sid in station_ids:
        hn = record(code(sid))["hn_cm"]
        k = offsets.get(sid, DEFAULT_OFFSET)
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            cm = hn.get(d + timedelta(days=k))
            flag = truth.QC_OK
            if cm is None or pd.isna(cm):
                flag = truth.QC_MISSING
            elif cm / CM_PER_IN > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": round(cm / CM_PER_IN, 3) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

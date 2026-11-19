"""ARPA Lombardia: automatic snow-depth sensors (nivometri), Lombard Alps.

Snow depth (whole cm, every 10 minutes) from ARPA Lombardia's hydro-nivo-
meteorological network: Livigno, Bormio, Santa Caterina, Aprica, Madesimo
(Alpe Motta), Valmalenco, Valcamonica, the Bergamo and Brescia Prealps.
Open data on dati.lombardia.it (Socrata, no key), CC0 1.0, credit
"ARPA Lombardia". Four datasets with one schema (idsensore, data, valore,
stato):

  5kig-ayub  up to 2010        id9e-7wf3  2011-2020
  uqbu-tt6m  2021 on (validated, a few months behind)
  647i-nhxk  the recent months (live)

Timestamps are local solar time (CET, UTC+1) all year.

New snow is derived from depth like Land Tirol (networks/lwdtirol.py): the
depth at the start of day D is the median of the valid readings from 23:00
on D-1 to 00:50 on D (local midnight, CET), and day D <- depth(D+1) -
depth(D) with the shared QC. These sensors report whole centimetres and
jitter by 1-2 cm, so rises under 2 cm count as 0 (1 cm for Tirol).

Station ids are `<idsensore>:IT:ARPAL`. Only the readings near midnight are
requested (one request per sensor per dataset per season); past seasons
are cached, the current one is re-fetched at most once per UTC day.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from ..stations import data_dir
from .lwdtirol import rise_rows

log = logging.getLogger(__name__)

API = "https://www.dati.lombardia.it/resource/{ds}.json"
# dataset -> (first, last) day it holds; None = open-ended
DATASETS = {
    "5kig-ayub": (None, date(2010, 12, 31)),
    "id9e-7wf3": (date(2011, 1, 1), date(2020, 12, 31)),
    "uqbu-tt6m": (date(2021, 1, 1), None),
    "647i-nhxk": (None, None),  # rolling recent months; asked only for recent seasons
}
RECENT_DAYS = 400
DEADBAND_CM = 2.0
RETRIES = 5
PAUSE_S = 0.5
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def season_of(d: date) -> int:
    return d.year if d.month >= 10 else d.year - 1


def _datasets(begin: date, end: date, today: date) -> list[str]:
    out = []
    for ds, (first, last) in DATASETS.items():
        if ds == "647i-nhxk":
            if end >= today - timedelta(days=RECENT_DAYS):
                out.append(ds)
        elif (first is None or end >= first) and (last is None or begin <= last):
            out.append(ds)
    return out


def _get(ds: str, sensor: str, begin: date, end: date) -> list[dict]:
    where = (f"idsensore='{sensor}' AND date_extract_hh(data) in (23,0) "
             f"AND data between '{begin}T00:00:00' and '{end}T23:59:59'")
    params = {"$where": where, "$order": "data", "$limit": 50000, "$select": "data,valore,stato"}
    for attempt in range(RETRIES):
        try:
            r = requests.get(API.format(ds=ds), params=params, headers=UA, timeout=180)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("ARPA Lombardia request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def midnight_depths(records: list[dict]) -> pd.Series:
    """Depth (cm) at the start of each local day: median of 23:00 D-1 .. 00:50 D."""
    if not records:
        return pd.Series(dtype=float)
    df = pd.DataFrame(records)
    if "stato" in df:
        df = df[df["stato"] == "VA"]
    t = pd.to_datetime(df["data"], errors="coerce")
    v = pd.to_numeric(df["valore"], errors="coerce")
    ok = (v.notna() & (v >= 0) & t.notna() & t.dt.hour.isin([23, 0])).to_numpy()
    day = (t + pd.Timedelta(hours=1)).dt.date  # 23:xx belongs to the next day's start
    s = pd.Series(v.to_numpy()[ok], index=day.to_numpy()[ok], dtype=float)
    return s.groupby(level=0).median()


def _season(sensor: str, year: int) -> pd.Series:
    """Midnight depths for the season starting 1 Oct `year` (cached)."""
    today = datetime.now(timezone.utc).date()
    # the validated archive runs months behind: last season is still filling
    # in, so it is re-read (at most daily) like the current one
    current = year >= season_of(today) - 1
    root = data_dir() / "cache" / "arpal" / sensor
    path = root / (f"{year}-{today:%Y%m%d}.csv" if current else f"{year}.csv")
    if path.exists():
        df = pd.read_csv(path)
        return pd.Series(df["depth_cm"].to_numpy(), index=pd.to_datetime(df["date"]).dt.date, dtype=float)
    if year > season_of(today):
        return pd.Series(dtype=float)
    begin, end = date(year, 9, 30), min(date(year + 1, 10, 1), today)
    recs: list[dict] = []
    for ds in _datasets(begin, end, today):
        recs += _get(ds, sensor, begin, end)
    s = midnight_depths(recs)
    root.mkdir(parents=True, exist_ok=True)
    if current:
        for old in root.glob(f"{year}-*.csv"):
            old.unlink()
    pd.DataFrame({"date": list(s.index), "depth_cm": s.to_numpy()}).to_csv(path, index=False)
    return s


def depths(sensor: str, begin: date, end: date) -> pd.Series:
    parts = [_season(sensor, y) for y in range(season_of(begin), season_of(end + timedelta(days=1)) + 1)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")].sort_index()


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from midnight-to-midnight depth rises."""
    rows = []
    for sid in station_ids:
        rows += rise_rows(sid, depths(code(sid), begin, end), begin, end, deadband_cm=DEADBAND_CM)
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

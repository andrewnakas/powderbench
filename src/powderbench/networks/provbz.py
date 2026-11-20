"""South Tyrol (Autonome Provinz Bozen - Südtirol / Provincia di Bolzano):
automatic snow-depth stations of the provincial weather and hydrographic
service.

Most stand on high pastures and ski slopes of the Dolomites and the Ötztal,
Ortler and Zillertal ranges (Alta Badia's Piz La Ila, Obereggen, Sulden,
Schnals glacier, Seiser Alm, Karerpass, Sexten...). Data: "Misure meteo e
idrografiche" (data.civis.bz.it), Provincia autonoma di Bolzano, CC0 1.0.

  GET https://geoservices.buergernetz.bz.it/services/meteo/v1/timeseries
      ?station_code=<SCODE>&sensor_code=HS&date_from=YYYYMMDD&date_to=YYYYMMDD

returns every 10-minute HS reading (cm), stamped in local time with a
"CET"/"CEST" suffix. One request per station per season (Sep-Aug).

New snow is derived from depth exactly as for Land Tirol
(networks/lwdtirol.py): the depth at the start of local day D is the median
of the readings from 22:40 to 23:20 UTC on D-1 (local midnight, CET), and
day D <- max(depth(D+1) - depth(D), 0) with the same deadband and QC.

Station ids are `<SCODE>:IT:PROVBZ` (e.g. 61690SF). Past seasons are cached
as derived midnight depths; the current season is re-fetched at most once
per UTC day.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from ..stations import data_dir
from .lwdtirol import midnight_depths, rise_rows

log = logging.getLogger(__name__)

API = "https://geoservices.buergernetz.bz.it/services/meteo/v1"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
RETRIES = 5
PAUSE_S = 1.0
_TZ = {"CEST": "+02:00", "CET": "+01:00"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def season_of(d: date) -> int:
    return d.year if d.month >= 9 else d.year - 1


def parse_series(records: list[dict]) -> pd.DataFrame:
    """timestamp (UTC), hs_cm from the timeseries JSON (negatives -> NaN)."""
    if not records:
        return pd.DataFrame(columns=["timestamp", "hs_cm"])
    df = pd.DataFrame(records)
    stamp = df["DATE"].astype(str)
    for name, off in _TZ.items():
        stamp = stamp.str.replace(rf"{name}$", off, regex=True)
    df["timestamp"] = pd.to_datetime(stamp, utc=True, errors="coerce", format="ISO8601")
    hs = pd.to_numeric(df["VALUE"], errors="coerce")
    df["hs_cm"] = hs.where(hs >= 0)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    return df[["timestamp", "hs_cm"]].drop_duplicates("timestamp").reset_index(drop=True)


def _get(path: str, **params) -> list | dict:
    for attempt in range(RETRIES):
        try:
            r = requests.get(f"{API}/{path}", params=params, headers=UA, timeout=180)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("South Tyrol meteo request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _season(scode: str, year: int) -> pd.Series:
    """Midnight depths for the season starting 1 Sep `year` (cached)."""
    today = datetime.now(timezone.utc).date()
    current = season_of(today) == year
    root = data_dir() / "cache" / "provbz" / scode
    path = root / (f"{year}-{today:%Y%m%d}.csv" if current else f"{year}.csv")
    if path.exists():
        df = pd.read_csv(path)
        return pd.Series(df["depth_cm"].to_numpy(), index=pd.to_datetime(df["date"]).dt.date, dtype=float)
    if year > season_of(today):
        return pd.Series(dtype=float)
    raw = parse_series(_get("timeseries", station_code=scode, sensor_code="HS",
                            date_from=f"{year}0901", date_to=f"{year + 1}0902"))
    s = midnight_depths(raw) if len(raw) else pd.Series(dtype=float)
    root.mkdir(parents=True, exist_ok=True)
    if current:
        for old in root.glob(f"{year}-*.csv"):
            old.unlink()
    pd.DataFrame({"date": list(s.index), "depth_cm": s.to_numpy()}).to_csv(path, index=False)
    return s


def depths(scode: str, begin: date, end: date) -> pd.Series:
    parts = [_season(scode, y) for y in range(season_of(begin), season_of(end + timedelta(days=1)) + 1)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")]


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from midnight-to-midnight depth rises."""
    rows = []
    for sid in station_ids:
        rows += rise_rows(sid, depths(code(sid), begin, end), begin, end)
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

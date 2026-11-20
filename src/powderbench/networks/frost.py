"""MET Norway (Norwegian Meteorological Institute) — daily snow depth via Frost.

Element `surface_snow_thickness` at P1D resolution with time offset PT6H: the
depth read at 06 UTC (07:00 CET) every morning, mostly by observers. Frost needs
a free client id (https://frost.met.no/auth/requestCredentials.html), read
from the FROST_CLIENT_ID environment variable and sent as the basic-auth
user. Data CC BY 4.0 / NLOD: credit "MET Norway" (Frost reports
creativecommons.org/licenses/by/3.0/no on every response).

New snow is derived from depth like SMHI: day D <- max(depth(D+k) -
depth(D+k-1), 0) with k = the station's day_offset (default 1: the
morning-to-morning rise, 07:00 D .. 07:00 D+1).

QC beyond the shared depth rules:
  - Frost's qualityCode 5 (very uncertain), 6 (model data) and 7 (erroneous)
    readings are not used. 0-4 are (2 and 4 = not yet controlled, which is
    every reading of the last few days).
  - Where the station also reports `sum(precipitation_amount P1D)`, a rise of
    DRIFT_RISE_CM or more with a gauge reading below DRY_MM is wind drift, not
    snowfall: QC_NO_SWE_SUPPORT. The gauge value dated D is the 24 h ending at
    06 UTC D (over winter 2025-26 it correlates 0.39 with the depth rise
    D-1 -> D and 0.03 with D -> D+1), so the rise d0 -> d1 is checked against
    the value dated d1. A missing gauge reading is no evidence either way.

Data: one request per station per season (Oct-Sep). Past seasons are cached;
the current one is re-fetched at most once per UTC day.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

URL = "https://frost.met.no/observations/v0.jsonld"
DEPTH = "surface_snow_thickness"
PRECIP = "sum(precipitation_amount P1D)"
IN_PER_CM = 1 / 2.54
BAD_QUALITY = {5, 6, 7}
DRIFT_RISE_CM = 10.0
DRY_MM = 0.5
RETRIES = 5
PAUSE_S = 0.2
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
COLUMNS = ["date", "depth_cm", "precip_mm"]


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def client_id() -> str:
    cid = os.environ.get("FROST_CLIENT_ID", "").strip()
    if not cid:
        raise RuntimeError("FROST_CLIENT_ID is not set: Norwegian truth needs a free MET Norway Frost client id "
                           "(https://frost.met.no/auth/requestCredentials.html)")
    return cid


def parse(payload: dict) -> pd.DataFrame:
    """date, depth_cm, precip_mm from a Frost observations response. Several
    sensors at one station (SN123:0, SN123:1) -> the lowest sensor number."""
    best: dict[tuple[date, str], tuple[str, float]] = {}
    for item in payload.get("data", []):
        d = datetime.fromisoformat(item["referenceTime"].replace("Z", "+00:00")).date()
        sensor = item.get("sourceId", "")
        for ob in item.get("observations", []):
            el = ob.get("elementId")
            if el not in (DEPTH, PRECIP) or ob.get("qualityCode") in BAD_QUALITY:
                continue
            try:
                v = float(ob["value"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (d, el)
            if key not in best or sensor < best[key][0]:
                best[key] = (sensor, v)
    rows: dict[date, dict] = {}
    for (d, el), (_, v) in best.items():
        rows.setdefault(d, {"date": d, "depth_cm": float("nan"), "precip_mm": float("nan")})
        rows[d]["depth_cm" if el == DEPTH else "precip_mm"] = v
    df = pd.DataFrame(sorted(rows.values(), key=lambda r: r["date"]), columns=COLUMNS)
    # Norwegian observers file -1 for "no snow"; a bare-ground reading is 0
    df.loc[df["depth_cm"] < 0, "depth_cm"] = 0.0
    return df


def _get(sn: str, begin: date, end: date) -> dict:
    params = {"sources": sn, "elements": f"{DEPTH},{PRECIP}", "referencetime": f"{begin}/{end}",
              "timeoffsets": "PT6H", "timeresolutions": "P1D",
              "fields": "sourceId,referenceTime,elementId,value,qualityCode"}
    for attempt in range(RETRIES):
        try:
            r = requests.get(URL, params=params, auth=(client_id(), ""), headers=UA, timeout=180)
            if r.status_code in (404, 412):  # "no data found" for the window
                return {"data": []}
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.json()
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("Frost request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _season(sn: str, start_year: int) -> pd.DataFrame:
    """Oct 1 start_year .. Sep 30 start_year+1 (to today at most)."""
    today = datetime.now(timezone.utc).date()
    begin, end = date(start_year, 10, 1), min(date(start_year + 1, 10, 1), today + timedelta(days=1))
    current = end > today
    root = data_dir() / "cache" / "frost" / sn
    tag = f"{start_year}" + (f"-{today:%Y%m%d}" if current else "")
    path = root / f"{tag}.csv"
    if path.exists():
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    if begin > today:
        return pd.DataFrame(columns=COLUMNS)
    df = parse(_get(sn, begin, end))
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(f"{start_year}-*.csv"):
        old.unlink()
    df.to_csv(path, index=False)
    return df


def record(sn: str, begin: date, end: date) -> pd.DataFrame:
    """date-indexed depth_cm, precip_mm for [begin, end]."""
    first = begin.year if begin.month >= 10 else begin.year - 1
    last = end.year if end.month >= 10 else end.year - 1
    df = pd.concat([_season(sn, y) for y in range(first, last + 1)], ignore_index=True)
    return df.drop_duplicates("date", keep="last").set_index("date")


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from 06 UTC depth deltas."""
    rows = []
    offsets = offsets or {}
    for sid in station_ids:
        k = offsets.get(sid, 1)
        rec = record(code(sid), begin + timedelta(days=k - 1), end + timedelta(days=k))
        dep, pr = rec["depth_cm"], rec["precip_mm"]
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            d0, d1 = d + timedelta(days=k - 1), d + timedelta(days=k)
            s0, s1 = dep.get(d0), dep.get(d1)
            p = pr.get(d1)  # gauge 06 UTC d0 .. 06 UTC d1 is dated d1
            flag = truth.QC_OK
            if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
                flag = truth.QC_MISSING
            elif s1 * IN_PER_CM > truth.MAX_DEPTH_IN:
                flag = truth.QC_BAD_DEPTH
            elif (s1 - s0) * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            elif s1 - s0 >= DRIFT_RISE_CM and p is not None and not pd.isna(p) and p < DRY_MM:
                flag = truth.QC_NO_SWE_SUPPORT
            valid = flag == truth.QC_OK
            snow = round(max((s1 - s0) * IN_PER_CM, 0.0), 3) if valid else float("nan")
            rows.append({"station_id": sid, "date": d, "snow24": snow, "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

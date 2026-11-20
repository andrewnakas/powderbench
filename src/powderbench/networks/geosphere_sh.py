"""GeoSphere Austria (formerly ZAMG) — daily snow depth from the automated
snow gauge at climate stations that have no hand measurement of new snow.

`sh` in dataset klima-v2-1d: Gesamtschneehöhe aus automatischer
Schneepegelmessung, offset-corrected, at observation term I (06 UTC, 07:00
CET); -1 = no snow. CC BY 4.0: credit "GeoSphere Austria"
(https://data.hub.geosphere.at).

New snow is derived from depth like the nordic networks: day D <-
max(depth(D+k) - depth(D+k-1), 0) with k = the station's day_offset (default
1: the rise 07:00 D .. 07:00 D+1). That matches the hand-measured network:
at the 61 stations that report both over winter 2025-26, the new snow filed
on D tracks the gauge rise D-1 -> D (median r 0.95; 0.17 for D -> D+1), i.e.
the report dated D+1 is the rise D -> D+1.

QC beyond the shared depth rules: `rr`, the 24 h precipitation from 06:01 UTC
on D to 06:00 UTC on D+1 (dated D, -1 = none), covers the same 24 h as the
rise D -> D+1. A rise of DRIFT_RISE_CM or more with under DRY_MM in the gauge
is wind drift or a gauge fault, not snowfall: QC_NO_SWE_SUPPORT. A missing
gauge value is no evidence either way. GeoSphere's quality flags (unchecked,
automatically or manually checked) all mark usable values.

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
PARAMS = "sh,rr"
IN_PER_CM = 1 / 2.54
DRIFT_RISE_CM = 10.0
DRY_MM = 0.5
BATCH = 40
RETRIES = 6
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
COLUMNS = ["station", "date", "sh", "rr"]


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
    """station, date, sh, rr for one calendar year."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "geosphere_sh"
    tag = f"{year}" if year < today.year else f"{year}-{today:%Y%m%d}"
    paths = {c: root / c / f"{tag}.csv" for c in codes}
    missing = [c for c, p in paths.items() if not p.exists()]
    for i in range(0, len(missing), BATCH):
        part = missing[i : i + BATCH]
        if date(year, 1, 1) > today:
            break
        end = min(date(year, 12, 31), today)
        df = _get({"station_ids": ",".join(part), "start": f"{year}-01-01", "end": end.isoformat()})
        df["station"] = df["station"].astype(str)
        for c in part:
            p = paths[c]
            p.parent.mkdir(parents=True, exist_ok=True)
            for old in p.parent.glob(f"{year}-*.csv"):  # yesterday's copy of the current year
                old.unlink()
            df[df["station"] == c].to_csv(p, index=False)
    frames = [pd.read_csv(p, dtype={"station": str}) for p in paths.values() if p.exists()]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not len(out):
        return pd.DataFrame(columns=COLUMNS)
    out["date"] = pd.to_datetime(out["time"], utc=True).dt.date
    return out[COLUMNS]


def record(station_ids: list[str], begin: date, end: date) -> dict[str, pd.DataFrame]:
    """station_id -> date-indexed depth_cm, precip_mm for [begin, end]
    (-1 "no snow" / "no precipitation" -> 0)."""
    codes = {code(s): s for s in station_ids}
    df = pd.concat([_year(list(codes), y) for y in range(begin.year, end.year + 1)], ignore_index=True)
    df = df[(df["date"] >= begin) & (df["date"] <= end)]
    sh = pd.to_numeric(df["sh"], errors="coerce")
    rr = pd.to_numeric(df["rr"], errors="coerce")
    df = df.assign(depth_cm=sh.where(sh != -1, 0.0), precip_mm=rr.where(rr != -1, 0.0))
    out = {}
    for c, sid in codes.items():
        g = df[df["station"] == c]
        out[sid] = g.drop_duplicates("date", keep="last").set_index("date")[["depth_cm", "precip_mm"]]
    return out


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from 06 UTC gauge-depth deltas."""
    offsets = offsets or {}
    lo = min([1, *offsets.values()])
    hi = max([1, *offsets.values()])
    recs = record(station_ids, begin + timedelta(days=lo - 1), end + timedelta(days=hi))
    rows = []
    for sid in station_ids:
        k = offsets.get(sid, 1)
        rec = recs.get(sid)
        dep = rec["depth_cm"] if rec is not None else pd.Series(dtype=float)
        pr = rec["precip_mm"] if rec is not None else pd.Series(dtype=float)
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            d0, d1 = d + timedelta(days=k - 1), d + timedelta(days=k)
            s0, s1 = dep.get(d0), dep.get(d1)
            p = pr.get(d0)  # rr dated d0 is 06:01 UTC d0 .. 06:00 UTC d1
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

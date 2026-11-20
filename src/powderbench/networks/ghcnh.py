"""NOAA GHCNh (Global Historical Climatology Network hourly): the morning
snow-depth group of the WMO weather reports (SYNOP), at mountain stations
worldwide.

Every WMO member's synoptic stations report total snow depth in the morning
report (06 UTC in Europe, 03 UTC in Central Asia, 00 UTC in China and Korea:
about 07-09 local everywhere). NCEI decodes them into GHCNh, the successor of
ISD, on AWS Open Data (`noaa-ghcnh-pds`, CC0 1.0, no key; NOAA asks for
credit). Year files are rewritten daily (~06:45 UTC) and run about two days
behind. WMO's Unified Data Policy (Resolution 1, 2021) makes these reports
core data: "free and unrestricted ... with no conditions on use".

New snow is derived from depth like SMHI: the morning reading on D+1 minus
the reading on D covers the morning of D to the morning of D+1, so day D <-
max(depth(D+1) - depth(D), 0), counted from 1 cm (the reports are whole
centimetres), with the shared depth QC (networks/lwdtirol.rise_rows).

The morning hour is fitted per station: of the synoptic hours that fall
05-11 local solar time, the earliest with at least 80% of the reports of the
best-reported one. Off-hour rows
in the live feed carry junk (Lomnický štít: 2340 mm at 07 UTC between 220
and 210 mm), and some morning rows too (10 mm on a 1000 mm pack, quality
code "passed"). So a reading counts only when it agrees, within
max(SPIKE_CM, SPIKE_FRAC x depth), with at least one neighbouring reading
at a synoptic hour (a real storm is confirmed by the readings after it; a
spike by none). Zero mornings inside a deep pack are dropouts, not melt
(`dropouts`), and a station that files many of them is left out by the
generator: its zeros cannot be told from bare ground.

Bare ground: many countries leave the depth group out of the report when
there is no snow, so a report with no depth is not always missing. It reads
as 0 cm only when the ground was bare or nearly so just before (the last
confirmed morning depth within FILL_DAYS was <= FILL_MAX_CM, or the station
reported no depth at all for the previous BARE_DAYS but did within
BARE_PREV_DAYS before, last winter, or does within BARE_NEAR_DAYS after: a
station that sends no depth group for years, as several Norwegian ones did
before 2014-2017, is not reporting bare ground), and never right before
a morning depth above FILL_RISE_MAX_CM (that is a gap in the reports, not a
storm). A report with no depth under a standing pack stays missing.

Station ids are `<GHCNh id>:GHCNH`. Data: one Parquet per station per year
(~0.3-0.5 MB, only the depth columns are kept, cached as CSV). Past years
are cached for good, the current year re-read at most once per UTC day.
"""

from __future__ import annotations

import bisect
import io
import logging
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from ..stations import data_dir
from .lwdtirol import rise_rows

log = logging.getLogger(__name__)

URL = "https://noaa-ghcnh-pds.s3.amazonaws.com/hourly/access/by-year/{year}/parquet/GHCNh_{gid}_{year}.parquet"
DEADBAND_CM = 1.0
SYNOPTIC_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
MORNING_LOCAL = (5.0, 11.0)  # local solar hours a morning reading may fall in
HOUR_SHARE = 0.8
DROPOUT_CM = 20.0  # a 0 between mornings this deep on both sides is a dropout
DROPOUT_DAYS = 5
SPIKE_CM = 25.0
SPIKE_FRAC = 0.25
BAD_QC = {"2", "3", "6", "7"}  # ISD-legacy codes: suspect / erroneous
FILL_DAYS = 20  # a last reading of a dusting, then reports without the group: bare (Puntijarka, Nov 2026)
FILL_MAX_CM = 5.0
BARE_DAYS = 20
BARE_NEAR_DAYS = 45  # ... and a depth report within this many days after,
BARE_PREV_DAYS = 300  # or within this many before (last winter's, for the dry autumn days)
FILL_RISE_MAX_CM = 60.0
RETRIES = 5
PAUSE_S = 0.2
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
COLS = ["DATE", "temperature", "snow_depth", "snow_depth_Quality_Code", "snow_depth_Source_Code"]
# Units differ by source. ISD/FM-12 SYNOP (220, 223, the rest) is in mm, with
# the SYNOP specials scaled too: 9970 (< 0.5 cm) and 9980 (patchy cover) read
# 0, 9990 (not measurable) missing. MET Norway's own feed (398, hourly) is in
# cm with -1 for no snow cover: before about 2023 it is the only depth source
# of most Norwegian stations, and read as mm it made their history ten times
# too dry. Poland's IMGW feed (395) files 0 at every hour it did not observe
# (and some whole winters of 0), so its depths are not used.
CM_SOURCES = {"398"}
CACHE_VERSION = "v2"  # bump when depth_rows changes: the cache holds its output
SKIP_SOURCES = {"395"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def _get(url: str) -> bytes | None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=180)
            if r.status_code in (403, 404):  # S3 says 403 for a key that does not exist
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
            log.warning("GHCNh request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def parse_parquet(blob: bytes) -> pd.DataFrame:
    """time (UTC, naive), depth_cm: the reports of one year file (depth_cm NaN:
    a report without the depth group)."""
    df = pd.read_parquet(io.BytesIO(blob), columns=COLS)
    return depth_rows(df)


def depth_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Reports (a temperature or a depth) at whole hours; a depth flagged
    suspect or erroneous drops the whole report (missing, not bare)."""
    temp = df["temperature"] if "temperature" in df else pd.Series(float("nan"), index=df.index)
    df = df[df["snow_depth"].notna() | temp.notna()]
    qc = df["snow_depth_Quality_Code"].astype("string").fillna("")
    df = df[~qc.isin(BAD_QC)]
    t = pd.to_datetime(df["DATE"], errors="coerce")
    raw = pd.to_numeric(df["snow_depth"], errors="coerce")
    src = (df["snow_depth_Source_Code"] if "snow_depth_Source_Code" in df
           else pd.Series("", index=df.index)).astype("string").fillna("").str.strip()
    cm = src.isin(CM_SOURCES)
    v = raw.where(cm, raw / 10.0)
    v = v.mask(cm & (raw == -1) | cm & raw.isin([997, 998]) | ~cm & raw.isin([9970, 9980]), 0.0)
    v = v.mask(src.isin(SKIP_SOURCES) | cm & (raw == 999) | ~cm & (raw == 9990))
    out = pd.DataFrame({"time": t, "depth_cm": v}).dropna(subset=["time"])
    out = out[~(out["depth_cm"] < 0) & (out["time"].dt.minute == 0)]
    return out.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def _year(gid: str, year: int) -> pd.DataFrame:
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "ghcnh" / CACHE_VERSION / gid
    current = year >= today.year
    path = root / (f"{year}-{today:%Y%m%d}.csv" if current else f"{year}.csv")
    if path.exists():
        df = pd.read_csv(path)
        df["time"] = pd.to_datetime(df["time"])
        return df
    if year > today.year:
        return pd.DataFrame(columns=["time", "depth_cm"])
    blob = _get(URL.format(gid=gid, year=year))
    df = parse_parquet(blob) if blob else pd.DataFrame(columns=["time", "depth_cm"])
    root.mkdir(parents=True, exist_ok=True)
    if current:
        for old in root.glob(f"{year}-*.csv"):
            old.unlink()
    df.to_csv(path, index=False)
    return df


def reports(gid: str, begin: date, end: date) -> pd.DataFrame:
    """Depth reports at synoptic hours, [begin - 1 day, end + 1 day]."""
    parts = [_year(gid, y) for y in range((begin - timedelta(days=1)).year, (end + timedelta(days=1)).year + 1)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame({"time": pd.Series(dtype="datetime64[ns]"), "depth_cm": pd.Series(dtype=float)})
    df = pd.concat(parts, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    lo, hi = pd.Timestamp(begin - timedelta(days=1)), pd.Timestamp(end + timedelta(days=2))
    df = df[(df["time"] >= lo) & (df["time"] < hi) & df["time"].dt.hour.isin(SYNOPTIC_HOURS)]
    return df.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def morning_hour(times: pd.Series, lon: float) -> int | None:
    """The earliest synoptic hour 05-11 local solar time with at least
    HOUR_SHARE of the reports of the best-reported such hour (Slovakia and
    Norway report depth at 06 and 09 UTC alike: 06 is the climate reading)."""
    if not len(times):
        return None
    counts = times.dt.hour.value_counts()
    ok = [h for h in counts.index if MORNING_LOCAL[0] <= (h + lon / 15.0) % 24 < MORNING_LOCAL[1]]
    if not ok:
        return None
    top = max(counts[h] for h in ok)
    return int(min((h for h in ok if counts[h] >= HOUR_SHARE * top), key=lambda h: (h + lon / 15.0) % 24))


def confirmed(depth: pd.Series) -> pd.Series:
    """Boolean: each reading agrees with at least one neighbouring reading."""
    v = depth.to_numpy(float)
    ok = np.zeros(len(v), bool)
    for i in range(len(v)):
        tol = max(SPIKE_CM, SPIKE_FRAC * v[i])
        prev_ok = i > 0 and abs(v[i] - v[i - 1]) <= tol
        next_ok = i + 1 < len(v) and abs(v[i] - v[i + 1]) <= tol
        ok[i] = prev_ok or next_ok
    return pd.Series(ok, index=depth.index)


def morning_depths(rep: pd.DataFrame, lon: float) -> pd.Series:
    """Morning depth (cm) by UTC date (= the local date: 07-09 local): the
    confirmed reports, plus 0 for a morning report without the depth group
    on bare ground (module docstring)."""
    dep = rep[rep["depth_cm"].notna()]
    if dep.empty:
        return pd.Series(dtype=float)
    hour = morning_hour(dep["time"], lon)
    if hour is None:
        return pd.Series(dtype=float)
    ok = confirmed(dep["depth_cm"])
    good = dep[ok.to_numpy()]
    m = good[good["time"].dt.hour == hour]
    depth = dict(zip(m["time"].dt.date, m["depth_cm"].astype(float)))
    any_depth = sorted(set(dep["time"].dt.date))
    mornings = rep[(rep["time"].dt.hour == hour) & rep["depth_cm"].isna()]
    filled: dict = {}
    conf_days = sorted(depth)
    for d in sorted(set(mornings["time"].dt.date)):
        if d in depth:
            continue
        k = bisect.bisect_left(conf_days, d)
        last_day, last_cm = (conf_days[k - 1], depth[conf_days[k - 1]]) if k else (None, None)
        j = bisect.bisect_left(any_depth, d)
        prev_report = any_depth[j - 1] if j else None
        near_bare = last_day is not None and (d - last_day).days <= FILL_DAYS and last_cm <= FILL_MAX_CM
        next_report = any_depth[j] if j < len(any_depth) else None
        reporting = ((prev_report is not None and (d - prev_report).days <= BARE_PREV_DAYS)
                     or (next_report is not None and (next_report - d).days <= BARE_NEAR_DAYS))
        bare_season = reporting and (prev_report is None or (d - prev_report).days > BARE_DAYS)
        if near_bare or bare_season:
            filled[d] = 0.0
    for d in list(filled):  # a fill right before a deep morning reading is a gap in the reports
        nxt = depth.get(d + timedelta(days=1))
        if nxt is not None and nxt > FILL_RISE_MAX_CM:
            del filled[d]
    out = pd.Series({**filled, **depth}, dtype=float).sort_index()
    return out[~dropouts(out)]


def dropouts(md: pd.Series) -> pd.Series:
    """Boolean: zero mornings inside a pack. Some stations file 0 when the
    depth cannot be read (Kasprowy Wierch, Kredarica): a 0 with a morning of
    >= DROPOUT_CM within DROPOUT_DAYS on both sides is not bare ground (a pack
    that deep does not melt out and come back within days)."""
    days = list(md.index)
    vals = md.to_numpy(float)
    out = np.zeros(len(vals), bool)
    for i, (d, v) in enumerate(zip(days, vals)):
        if v != 0:
            continue
        prev = [vals[j] for j in range(i - 1, -1, -1) if vals[j] > 0 and (d - days[j]).days <= DROPOUT_DAYS][:1]
        nxt = [vals[j] for j in range(i + 1, len(vals)) if vals[j] > 0 and (days[j] - d).days <= DROPOUT_DAYS][:1]
        out[i] = bool(prev and nxt and prev[0] >= DROPOUT_CM and nxt[0] >= DROPOUT_CM)
    return pd.Series(out, index=md.index)


def daily(station_ids: list[str], begin: date, end: date, lons: dict[str, float] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from morning-to-morning depth rises.
    The morning hour is fitted on at least the year up to `end`, so a short
    live window reads the same hour as the climatology."""
    lons = lons or {}
    rows = []
    for sid in station_ids:
        rep = reports(code(sid), min(begin, end - timedelta(days=365)), end + timedelta(days=1))
        rows += rise_rows(sid, morning_depths(rep, lons.get(sid, 15.0)), begin, end, deadband_cm=DEADBAND_CM)
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

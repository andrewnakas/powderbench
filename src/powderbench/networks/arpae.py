"""Arpae Emilia-Romagna — snow depth at the automatic snow stations of the
northern Apennines (Corno alle Scale, Cimone, Passo delle Radici, Febbio…).
CC BY 4.0: credit "Arpae Emilia-Romagna" (https://dati.arpae.it).

One feed, no key: dati-simc.arpae.it/opendata/osservati/meteo/storico/
YYYY-MM.json.gz, every station and variable of the regional network for one
month as JSON lines (one line per station and time, BUFR codes; the current
month is rewritten through the day). Snow depth is B13013, in metres, about
every 30 minutes, UTC. Only those values are kept, per month, in
data/cache/arpae/YYYY-MM.csv: past months once, the last two months at most
once per UTC day.

A station has no code of its own: it is (network, lon, lat), with lon/lat in
1e-5 degrees, as Arpae writes them. The station id is built from them.

Depth at a day's 06 UTC (07:00 CET, the morning-board time of the other
networks) is the median of the readings from 05:00 to 07:00 UTC. New snow is
derived from depth like the nordic networks: day D <- max(depth(D+k) -
depth(D+k-1), 0) with k = the station's day_offset (default 1: the rise 06 UTC
D .. 06 UTC D+1). The sensors report whole centimetres and jitter, so rises
under MIN_RISE_CM count as 0 (as for ARPA Lombardia). There is no snow-water
or precipitation value beside the depth, so the shared depth rules are the
only QC.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

MONTH_URL = "https://dati-simc.arpae.it/opendata/osservati/meteo/storico/{month}.json.gz"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
SNOW_DEPTH = "B13013"  # total snow depth, m
NAME, HEIGHT, LAT, LON = "B01019", "B07030", "B05001", "B06001"
TERM_FROM, TERM_TO = 5 * 60, 7 * 60  # minutes after 00 UTC: the 06 UTC depth window
MIN_RISE_CM = 2.0
IN_PER_CM = 1 / 2.54
LIVE_MONTHS = 2  # the current and the previous month are re-read daily
RETRIES = 4
TIMEOUT_S = 300
SUFFIX = ":IT:ARPAE"
COLUMNS = ["sid", "name", "height_m", "lat", "lon", "time", "depth_m"]


def station_id(network: str, lon: int, lat: int) -> str:
    return f"{network}-{lon}-{lat}{SUFFIX}"


def _cache_path(month: str):
    p = data_dir() / "cache" / "arpae" / f"{month}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _fresh(path, month: str, today: date) -> bool:
    if not path.exists():
        return False
    y, m = (int(x) for x in month.split("-"))
    months_ago = (today.year - y) * 12 + today.month - m
    if months_ago >= LIVE_MONTHS:
        # a finished month is final, but only if it was read after it ended
        written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()
        return written > date(y + (m == 12), m % 12 + 1, 1)
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date() == today


def parse(lines) -> pd.DataFrame:
    """Snow-depth rows from a month's JSON lines."""
    rows = []
    for line in lines:
        if SNOW_DEPTH not in line:
            continue
        r = json.loads(line)
        meta = r["data"][0]["vars"]
        sid = station_id(r["network"], r["lon"], r["lat"])
        for blk in r["data"][1:]:
            v = (blk.get("vars") or {}).get(SNOW_DEPTH)
            if v is None or v.get("v") is None:
                continue
            rows.append({"sid": sid, "name": (meta.get(NAME) or {}).get("v"),
                         "height_m": (meta.get(HEIGHT) or {}).get("v"),
                         "lat": r["lat"] / 1e5, "lon": r["lon"] / 1e5,
                         "time": r["date"], "depth_m": float(v["v"])})
    return pd.DataFrame(rows, columns=COLUMNS)


def month(month: str, today: date | None = None) -> pd.DataFrame:
    """One month's snow-depth readings (every station), cached."""
    today = today or datetime.now(timezone.utc).date()
    path = _cache_path(month)
    if not _fresh(path, month, today):
        for attempt in range(RETRIES):
            try:
                r = requests.get(MONTH_URL.format(month=month), headers=UA, timeout=TIMEOUT_S)
                if r.status_code == 404:
                    return pd.DataFrame(columns=COLUMNS)
                r.raise_for_status()
                with gzip.open(io.BytesIO(r.content), "rt", encoding="utf-8") as f:
                    df = parse(f)
                tmp = path.with_suffix(".tmp")
                df.to_csv(tmp, index=False)
                tmp.replace(path)
                break
            except (requests.RequestException, OSError, ValueError) as exc:
                if attempt == RETRIES - 1:
                    if path.exists():  # stale beats nothing
                        log.warning("Arpae %s: %s; using the cached copy", month, exc)
                        break
                    raise
                time.sleep(5 * 2**attempt)
    return pd.read_csv(path)


def _months(begin: date, end: date) -> list[str]:
    out, d = [], date(begin.year, begin.month, 1)
    while d <= end:
        out.append(f"{d:%Y-%m}")
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def readings(begin: date, end: date) -> pd.DataFrame:
    """Every station's readings over the months covering [begin, end]."""
    frames = [month(m) for m in _months(begin, end)]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)


def morning_depths(df: pd.DataFrame) -> dict[str, dict[date, float]]:
    """{station: {day: depth (cm) at 06 UTC}}: median of 05:00-07:00 UTC readings."""
    if not len(df):
        return {}
    t = pd.to_datetime(df["time"], utc=True)
    minute = t.dt.hour * 60 + t.dt.minute
    sel = df[(minute >= TERM_FROM) & (minute <= TERM_TO) & (df["depth_m"] >= 0)].copy()
    sel["day"] = t[sel.index].dt.date
    med = sel.groupby(["sid", "day"])["depth_m"].median() * 100
    out: dict[str, dict[date, float]] = {}
    for (sid, day), cm in med.items():
        out.setdefault(sid, {})[day] = float(cm)
    return out


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from 06 UTC depth deltas."""
    offsets = offsets or {}
    k_max = max([offsets.get(s, 1) for s in station_ids] or [1])
    deps = morning_depths(readings(begin - timedelta(days=1), end + timedelta(days=k_max)))
    rows = []
    for sid in station_ids:
        k = offsets.get(sid, 1)
        dep = deps.get(sid, {})
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            s0, s1 = dep.get(d + timedelta(days=k - 1)), dep.get(d + timedelta(days=k))
            flag = truth.QC_OK
            if s0 is None or s1 is None:
                flag = truth.QC_MISSING
            elif s1 * IN_PER_CM > truth.MAX_DEPTH_IN:
                flag = truth.QC_BAD_DEPTH
            elif (s1 - s0) * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rise = s1 - s0 if valid else 0.0
            snow = round(rise * IN_PER_CM, 3) if valid and rise >= MIN_RISE_CM else (0.0 if valid else float("nan"))
            rows.append({"station_id": sid, "date": d, "snow24": snow, "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

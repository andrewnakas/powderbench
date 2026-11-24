"""Daily model snowfall from Open-Meteo's own open-data archive (s3://openmeteo,
AWS Open Data, CC BY 4.0), the files the API reads — no API calls, no per-call
budget. Used by scripts/observers_gate.py for ECMWF IFS 9 km (`ecmwf_ifs`).

The archive reader, the O1280 point search and the cell choice are copied from
PR #876 (src/powderbench/nwp/omdata.py and grib.terrain_index), trimmed to the
grids needed here (octahedral reduced Gaussian, regular lat/lon); swap in
powderbench.nwp.omdata once that is on main. Needs `omfiles`, `s3fs` and
`tzfpy` (pip install omfiles s3fs tzfpy).

Daily values follow the historical-forecast API's daily snowfall_sum:
  - cell: Open-Meteo's terrain-optimised search on the model's own terrain;
  - elevation rule: where that cell's terrain is more than 100 m above or below
    the station, the hourly 2 m temperature is moved to the station's height
    (6.5 K/km) and snowfall kept only while it is below 0 C;
  - snowfall = 0.7 cm per mm of snowfall water equivalent;
  - days: the station's local days at a fixed UTC offset, the zone's offset at
    the start of the request (the API holds one offset per request).
Values at an hourly stamp cover the hour ending there.
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

BUCKET = "openmeteo"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LAPSE_K_PER_M = 0.0065
RULE_MIN_DZ_M = 100.0
CM_PER_MM_WE = 0.7
CM_PER_INCH = 2.54
WORKERS = 4  # polite: point reads are a few small range requests each


# --- archive reader (PR #876, nwp/omdata.py) ----------------------------------

@lru_cache(maxsize=1)
def _fs():
    import s3fs

    return s3fs.S3FileSystem(anon=True, default_block_size=1 << 16)


def _reader(path: str):
    import omfiles

    return omfiles.OmFileReader.from_fsspec(_fs(), f"{BUCKET}/{path}")


_local = threading.local()


def _thread_reader(path: str):
    """One open reader per file and thread (PR #876): every station reads the
    same few dozen chunk files, and opening one fetches its index."""
    cache = getattr(_local, "readers", None)
    if cache is None:
        cache = _local.readers = {}
    if path not in cache:
        if len(cache) >= 96:
            cache.clear()
        cache[path] = _reader(path)
    return cache[path]


@lru_cache(maxsize=None)
def meta(domain: str) -> dict:
    with _fs().open(f"{BUCKET}/data/{domain}/static/meta.json") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def listing(domain: str, variable: str) -> frozenset[str]:
    return frozenset(p.rsplit("/", 1)[-1] for p in _fs().ls(f"{BUCKET}/data/{domain}/{variable}", detail=False))


def _round(v):
    """Swift's round(): halves go away from zero."""
    v = np.asarray(v, float)
    return np.where(v >= 0, np.floor(v + 0.5), np.ceil(v - 0.5)).astype(np.int64)


class OctahedralGrid:
    """ECMWF's octahedral reduced Gaussian grid as Open-Meteo stores it
    (Domains/GaussianGrid.swift): one row of points, latitude lines from north
    to south, 20 + 4y points on the y-th line from either pole, each line
    starting at 0 deg E, lines spaced 180 / (2 L + 0.5) deg; Open-Meteo searches
    the 3 x 3 points around a place."""

    def __init__(self, lines: int):
        self.lines = lines
        self.count = 4 * lines * (lines + 9)
        self.dy = 180.0 / (2 * lines + 0.5)

    def nx(self, y):
        y = np.asarray(y, np.int64)
        return np.where(y < self.lines, 20 + 4 * y, (2 * self.lines - y - 1) * 4 + 20)

    def first(self, y):
        y = np.asarray(y, np.int64)
        m = 2 * self.lines - y
        return np.where(y < self.lines, 2 * y * y + 18 * y, self.count - (2 * m * m + 18 * m))

    def latitude(self, y):
        return (self.lines - np.asarray(y, float) - 1) * self.dy + self.dy / 2

    def around(self, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
        cy = int(np.clip(_round(self.lines - 1 - (lat - self.dy / 2) / self.dy), 1, 2 * self.lines - 2))
        cells, km = [], []
        for y in (cy - 1, cy, cy + 1):
            nx = int(self.nx(y))
            dx = 360.0 / nx
            xc = int(_round(lon / dx))
            for x in (xc - 1, xc, xc + 1):
                cells.append(int(self.first(y)) + (x + 2 * nx) % nx)
                km.append(float(np.hypot(self.latitude(y) - lat, x * dx - lon)) * 111.0)
        return np.array(cells, np.int64), np.array(km)

    def nearest(self, lat: float, lon: float) -> int:
        cells, km = self.around(lat, lon)
        return int(cells[int(np.argmin(km))])


@lru_cache(maxsize=None)
def grid(domain: str):
    """(grid, terrain height m (flat, NaN over sea), land mask (flat))."""
    hsurf = np.asarray(_reader(f"data/{domain}/static/HSURF.om")[:, :], dtype=float)
    nj, ni = hsurf.shape
    wkt = meta(domain)["crs_wkt"]
    land = (hsurf > -999.0).astype(float).ravel()
    height = np.where(hsurf > -999.0, hsurf, np.nan).ravel()
    octa = re.search(r"Reduced Gaussian Grid O(\d+) \(", wkt)
    if not octa:
        raise ValueError(f"{domain}: only octahedral reduced Gaussian grids are read here")
    g = OctahedralGrid(int(octa.group(1)))
    if g.count != ni * nj:
        raise ValueError(f"{domain}: {ni * nj} points, O{g.lines} has {g.count}")
    return {"grid": g, "Ni": ni}, height, land


# Open-Meteo's terrain-optimised cell search (Gridable.swift,
# findPointTerrainOptimised; PR #876 grib.terrain_index): keep the nearest cell
# when its terrain is within KEEP_M of the station, else the land cell
# minimising |height difference| + PENALTY_M_PER_KM x distance among the 3 x 3
# around — the nearest cell included, with its own distance penalty (which is
# how the API's chosen grid point comes out for a station 784 m above its
# nearest IFS cell; #876 scores that cell without the penalty) — unless the
# best is still more than GIVE_UP_M off.
KEEP_M, PENALTY_M_PER_KM, MAX_KM, GIVE_UP_M = 100.0, 30.0, 50.0, 1500.0


def cell_for(domain: str, lat: float, lon: float, elev_m: float) -> int:
    g, height, land = grid(domain)
    gg = g["grid"]
    b = gg.nearest(lat, lon)
    if land[b] >= 0.5 and np.isfinite(height[b]) and abs(height[b] - elev_m) <= KEEP_M:
        return b
    best, best_delta = None, np.inf
    for c, km in zip(*gg.around(lat, lon)):
        if not land[c] >= 0.5 or not np.isfinite(height[c]):
            continue
        delta = abs(height[c] - elev_m) + km * PENALTY_M_PER_KM
        if delta < best_delta and km < MAX_KM:
            best_delta, best = delta, int(c)
    return best if best is not None and best_delta <= GIVE_UP_M else b


def _pieces(domain: str, variable: str, start: datetime, end: datetime):
    """(file, first hour index in file, hours, first stamp) covering [start, end)."""
    files = listing(domain, variable)
    length = int(meta(domain).get("chunk_time_length") or 0)
    t = start
    while t < end:
        chunk = int((t - EPOCH).total_seconds() // 3600 // length) if length else None
        if chunk is not None and f"chunk_{chunk}.om" in files:
            c0 = EPOCH + timedelta(hours=chunk * length)
            stop = min(end, c0 + timedelta(hours=length))
            yield f"chunk_{chunk}.om", int((t - c0).total_seconds() // 3600), int((stop - t).total_seconds() // 3600), t
        elif f"year_{t.year}.om" in files:
            y0 = datetime(t.year, 1, 1, tzinfo=timezone.utc)
            stop = min(end, datetime(t.year + 1, 1, 1, tzinfo=timezone.utc))
            yield f"year_{t.year}.om", int((t - y0).total_seconds() // 3600), int((stop - t).total_seconds() // 3600), t
        else:
            raise FileNotFoundError(f"{domain}/{variable}: no file for {t:%Y-%m-%d %H}Z")
        t = stop


def series(domain: str, variable: str, cell: int, start: datetime, end: datetime) -> pd.Series:
    """Hourly values at one grid cell over [start, end), UTC stamps."""
    ni = int(grid(domain)[0]["Ni"])
    j, i = divmod(int(cell), ni)
    parts = []
    for name, h0, n, t0 in _pieces(domain, variable, start, end):
        v = np.asarray(_thread_reader(f"data/{domain}/{variable}/{name}")[j, i, h0:h0 + n], dtype=float)
        parts.append(pd.Series(v, index=pd.date_range(t0, periods=len(v), freq="h")))
    return pd.concat(parts) if parts else pd.Series(dtype=float)


# --- Open-Meteo's daily snowfall ------------------------------------------------

def below_zero_share(t0: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """Share of an interval below 0 C, the temperature linear between its ends."""
    t0, t1 = np.asarray(t0, float), np.asarray(t1, float)
    mixed = (t0 < 0) != (t1 < 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cross = np.where(t0 < 0, -t0 / (t1 - t0), -t1 / (t0 - t1))
    return np.where(mixed, cross, np.where(t0 < 0, 1.0, 0.0))


@lru_cache(maxsize=None)
def zone_name(lat: float, lon: float) -> str:
    import tzfpy

    return tzfpy.get_tz(lon, lat) or ""


def utc_offset(lat: float, lon: float, at: date) -> timedelta:
    name = zone_name(lat, lon)
    if not name:
        return timedelta(hours=round(lon / 15))
    return datetime(at.year, at.month, at.day, 12, tzinfo=ZoneInfo(name)).utcoffset()


def station_daily(domain: str, lat: float, lon: float, elev_m: float, begin: date, end: date,
                  rule: bool = True) -> tuple[pd.Series, dict]:
    """Daily snowfall (inches) for local days [begin, end] and how it was made."""
    off = utc_offset(lat, lon, begin)
    cell = cell_for(domain, lat, lon, elev_m)
    dz = float(grid(domain)[1][cell]) - elev_m
    t0 = datetime(begin.year, begin.month, begin.day, tzinfo=timezone.utc) - off
    t1 = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) - off + timedelta(days=1)
    swe = series(domain, "snowfall_water_equivalent", cell, t0 + timedelta(hours=1), t1 + timedelta(hours=1)).clip(lower=0)
    applied = rule and abs(dz) > RULE_MIN_DZ_M
    if applied:
        t2 = series(domain, "temperature_2m", cell, t0, t1 + timedelta(hours=1)) + dz * LAPSE_K_PER_M
        share = below_zero_share(t2.to_numpy()[:-1], t2.to_numpy()[1:])
        swe = swe * share
    local = swe.index - timedelta(hours=1) + off  # hour ending at the stamp -> its local start
    days = pd.Series(swe.to_numpy() * CM_PER_MM_WE / CM_PER_INCH, index=local.date)
    daily = days.groupby(level=0).sum(min_count=24)
    daily = daily[(daily.index >= begin) & (daily.index <= end)]
    return daily, {"cell": cell, "dz_m": round(dz), "rule": applied, "zone": zone_name(lat, lon),
                   "utc_offset_h": off.total_seconds() / 3600}


def daily_snowfall(stations: list[dict], begin: date, end: date, domain: str = "ecmwf_ifs",
                   workers: int = WORKERS) -> tuple[pd.DataFrame, dict]:
    """stations: dicts with station_id, latitude, longitude, elevation_m.
    -> (date x station_id inches, {station_id: info})."""
    grid(domain)

    def one(s):
        return s["station_id"], *station_daily(domain, s["latitude"], s["longitude"], s["elevation_m"], begin, end)

    with ThreadPoolExecutor(workers) as pool:
        out = list(pool.map(one, stations))
    frame = pd.DataFrame({sid: d for sid, d, _ in out})
    return frame, {sid: info for sid, _, info in out}

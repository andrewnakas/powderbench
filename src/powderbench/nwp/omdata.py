"""Open-Meteo's own open-data archive (s3://openmeteo, AWS Open Data, CC BY 4.0):
the files Open-Meteo's API reads, fetched directly. No key and no per-call
budget, and a point series is a few small range reads (0.4 s for a station-year
of hourly ERA5), so long builds need no API calls at all.

Layout (verified 2026-11-21):
  data/<domain>/static/HSURF.om         model terrain height (m); -999 over sea
  data/<domain>/static/meta.json        crs_wkt (grid bounding box), chunk_time_length
  data/<domain>/<variable>/year_YYYY.om [lat, lon, hour of year], older years
  data/<domain>/<variable>/chunk_N.om   [lat, lon, hour], hours N*L .. (N+1)*L-1
                                        since 1970-01-01 (L = chunk_time_length)
Values at a stamp cover the hour ending there (precipitation and snowfall
water equivalent in mm). Reading needs the optional `omfiles` and `s3fs`
packages (pip install "powderbench[omdata]").

Regular latitude/longitude domains need nothing more; rotated ones (GEM
RDPS/HRDPS, MeteoSwiss ICON-CH, KNMI) use Open-Meteo's own rotation,
Lambert ones (MET Nordic, DMI, HRRR, NBM) pyproj, and ECMWF IFS at 9 km
(O1280) Open-Meteo's own reduced Gaussian point search.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BUCKET = "openmeteo"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@lru_cache(maxsize=1)
def _fs():
    import s3fs

    return s3fs.S3FileSystem(anon=True, default_block_size=1 << 16)


def _reader(path: str):
    import omfiles

    return omfiles.OmFileReader.from_fsspec(_fs(), f"{BUCKET}/{path}")


@lru_cache(maxsize=None)
def meta(domain: str) -> dict:
    with _fs().open(f"{BUCKET}/data/{domain}/static/meta.json") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def listing(domain: str, variable: str) -> frozenset[str]:
    return frozenset(p.rsplit("/", 1)[-1] for p in _fs().ls(f"{BUCKET}/data/{domain}/{variable}", detail=False))


@lru_cache(maxsize=None)
def grid(domain: str) -> tuple[dict, np.ndarray, np.ndarray]:
    """(grid metadata for grib.terrain_index, terrain height m (flat), land
    fraction (flat: 1 where HSURF > -999)). Regular lat/lon domains get GRIB
    style keys; rotated and Lambert ones a "projected" grid whose station
    positions come from the domain's WKT through pyproj. The WKT's BBOX holds
    the first and last grid points (lat, lon), which fix origin and spacing,
    except for the rotated grids in ROTATED_ORIGINS."""
    hsurf = np.asarray(_reader(f"data/{TERRAIN_FROM.get(domain, domain)}/static/HSURF.om")[:, :], dtype=float)
    nj, ni = hsurf.shape
    wkt = meta(domain)["crs_wkt"]
    bbox = re.search(r"BBOX\[([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\]", wkt)
    if not bbox:
        raise ValueError(f"{domain}: no grid corners in its WKT")
    lat0, lon0, lat1, lon1 = (float(v) for v in bbox.groups())
    land = (hsurf > -999.0).astype(float).ravel()
    height = np.where(hsurf > -999.0, hsurf, np.nan).ravel()
    common = {"Ni": ni, "Nj": nj, "jScansPositively": 1, "iScansNegatively": 0, "jPointsAreConsecutive": 0}
    if "Reduced Gaussian" in wkt:  # one row of points (ECMWF IFS at 9 km)
        octa = re.search(r"Reduced Gaussian Grid O(\d+) \(", wkt)
        if not octa:
            raise ValueError(f"{domain}: only octahedral reduced Gaussian grids are read")
        gg = OctahedralGrid(int(octa.group(1)))
        if gg.count != ni * nj:
            raise ValueError(f"{domain}: {ni * nj} points, O{gg.lines} has {gg.count}")
        return {"gridType": "points", **common, "nearest": gg.nearest, "around": gg.around}, height, land
    if "METHOD[" not in wkt:  # plain latitude/longitude
        return {"gridType": "regular_ll", **common,
                "latitudeOfFirstGridPointInDegrees": lat0, "longitudeOfFirstGridPointInDegrees": lon0,
                "iDirectionIncrementInDegrees": (lon1 - lon0) / (ni - 1),
                "jDirectionIncrementInDegrees": (lat1 - lat0) / (nj - 1)}, height, land
    if "ob_tran" in wkt:  # rotated lat/lon, in Open-Meteo's own convention
        o_lat_p = float(re.search(r'"o_lat_p",([-\d.]+)', wkt).group(1))
        lon_0 = float(re.search(r'"lon_0",([-\d.]+)', wkt).group(1))
        rot = rotated_forward(-o_lat_p, lon_0)
        if domain in ROTATED_ORIGINS:
            x0, y0, dx, dy = ROTATED_ORIGINS[domain]
        else:  # the WKT's BBOX is the first and last grid point
            (x0, x1), (y0, y1) = rot(np.array([lat0, lat1]), np.array([lon0, lon1]))
            dx, dy = (x1 - x0) / (ni - 1), (y1 - y0) / (nj - 1)

        def ij(lats: np.ndarray, lons: np.ndarray):
            x, y = rot(lats, lons)
            return (x - x0) / dx, (y - y0) / dy

        scale = 111_195.0  # metres per rotated degree
    else:  # Lambert and other planar projections
        import pyproj  # optional dependency (pip install "powderbench[omdata]")

        crs = pyproj.CRS.from_wkt(wkt)
        to_grid = pyproj.Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
        (x0, x1), (y0, y1) = to_grid.transform([lon0, lon1], [lat0, lat1])
        dx, dy = (x1 - x0) / (ni - 1), (y1 - y0) / (nj - 1)

        def ij(lats: np.ndarray, lons: np.ndarray):
            x, y = to_grid.transform(lons, lats)
            return (np.asarray(x, float) - x0) / dx, (np.asarray(y, float) - y0) / dy

        scale = 1.0
    return {"gridType": "projected", **common, "ij": ij,
            "DxInMetres": abs(dx) * scale, "DyInMetres": abs(dy) * scale}, height, land


# Domains stored without their own terrain, and a domain on the same grid whose
# terrain stands in (NOAA's AI GFS and hybrid GEFS run on GFS's 0.25 deg grid,
# initialised from its analysis).
TERRAIN_FROM = {"ncep_aigfs025": "ncep_gfs025", "ncep_hgefs025_ensemble_mean": "ncep_gfs025"}


# Rotated grids that Open-Meteo defines by origin and spacing rather than by
# corners (Sources/App/Gem/GemDomain.swift, MeteoSwiss/MeteoSwissDomain.swift):
# (x0, y0, dx, dy) in rotated degrees.
ROTATED_ORIGINS = {
    "cmc_gem_rdps_10km": (306.141 - 360, -48.806, 0.090298, 0.090298),
    "cmc_gem_hrdps_west": (-22.18489, 5.308595 + 1180 * -0.00899, 0.00899, 0.00899),
    "meteoswiss_icon_ch1": (-6.46, -4.06, 0.01, 0.01),
    "meteoswiss_icon_ch2": (-6.46, -4.06, 0.02, 0.02),
}


def _round(v):
    """Swift's round(): halves go away from zero."""
    v = np.asarray(v, float)
    return np.where(v >= 0, np.floor(v + 0.5), np.ceil(v - 0.5)).astype(np.int64)


class OctahedralGrid:
    """ECMWF's octahedral reduced Gaussian grid as Open-Meteo stores it
    (Domains/GaussianGrid.swift): one row of points, latitude lines from
    north to south, 20 + 4y points on the y-th line from either pole, each line
    starting at 0 deg E. Open-Meteo spaces the lines evenly (180 / (2 L + 0.5)
    deg) and searches the 3 x 3 points around a place, as ported here."""

    def __init__(self, lines: int):
        self.lines = lines
        self.count = 4 * lines * (lines + 9)
        self.dy = 180.0 / (2 * lines + 0.5)

    def nx(self, y):
        y = np.asarray(y, np.int64)
        return np.where(y < self.lines, 20 + 4 * y, (2 * self.lines - y - 1) * 4 + 20)

    def first(self, y):
        """Index of line y's first point."""
        y = np.asarray(y, np.int64)
        m = 2 * self.lines - y
        return np.where(y < self.lines, 2 * y * y + 18 * y, self.count - (2 * m * m + 18 * m))

    def latitude(self, y):
        return (self.lines - np.asarray(y, float) - 1) * self.dy + self.dy / 2

    def around(self, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
        """The 3 x 3 points around (lat, lon): flat indices and km away."""
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

    def nearest(self, lats, lons) -> np.ndarray:
        out = []
        for lat, lon in zip(np.atleast_1d(lats), np.atleast_1d(lons)):
            cells, km = self.around(float(lat), float(lon))
            out.append(cells[int(np.argmin(km))])
        return np.array(out, np.int64)

    def coordinates(self, cell: int) -> tuple[float, float]:
        """(lat, lon) of a flat index."""
        half = self.count // 2
        if cell < half:
            y = int((np.sqrt(2 * cell + 81) - 9) / 2)
        else:
            y = 2 * self.lines - 1 - int((np.sqrt(2 * (self.count - cell - 1) + 81) - 9) / 2)
        lon = (cell - int(self.first(y))) * 360.0 / int(self.nx(y))
        return float(self.latitude(y)), lon - 360.0 if lon >= 180 else lon


def rotated_forward(latitude: float, longitude: float):
    """Open-Meteo's RotatedLatLonProjection.forward (Domains/RotatedLatLon.swift):
    (lats, lons) -> rotated (x, y) degrees. Its WKT writes o_lat_p = -latitude
    and lon_0 = longitude, which PROJ reads differently, so it is ported here."""
    th, ph = np.radians(90.0 + latitude), np.radians(longitude)

    def fwd(lats, lons):
        lat, lon = np.radians(np.asarray(lats, float)), np.radians(np.asarray(lons, float))
        x, y, z = np.cos(lon) * np.cos(lat), np.sin(lon) * np.cos(lat), np.sin(lat)
        x2 = np.cos(th) * np.cos(ph) * x + np.cos(th) * np.sin(ph) * y + np.sin(th) * z
        y2 = -np.sin(ph) * x + np.cos(ph) * y
        z2 = -np.sin(th) * np.cos(ph) * x - np.sin(th) * np.sin(ph) * y + np.cos(th) * z
        return -np.degrees(np.arctan2(y2, x2)), -np.degrees(np.arcsin(z2))

    return fwd


def _pieces(domain: str, variable: str, start: datetime, end: datetime):
    """(file, first hour index in file, hours, first stamp) covering [start, end)."""
    files = listing(domain, variable)
    length = int(meta(domain).get("chunk_time_length") or 0)
    t = start
    while t < end:
        year_file = f"year_{t.year}.om"
        chunk = int((t - EPOCH).total_seconds() // 3600 // length) if length else None
        chunk_file = f"chunk_{chunk}.om" if chunk is not None else None
        if chunk_file in files:
            c0 = EPOCH + timedelta(hours=chunk * length)
            stop = min(end, c0 + timedelta(hours=length))
            yield chunk_file, int((t - c0).total_seconds() // 3600), int((stop - t).total_seconds() // 3600), t
        elif year_file in files:
            y0 = datetime(t.year, 1, 1, tzinfo=timezone.utc)
            stop = min(end, datetime(t.year + 1, 1, 1, tzinfo=timezone.utc))
            yield year_file, int((t - y0).total_seconds() // 3600), int((stop - t).total_seconds() // 3600), t
        else:
            raise FileNotFoundError(f"{domain}/{variable}: no file for {t:%Y-%m-%d %H}Z")
        t = stop


def series(domain: str, variable: str, cell: int, start: datetime, end: datetime) -> pd.Series:
    """Hourly values at one grid cell (flat index) over [start, end), UTC stamps."""
    g = grid(domain)[0]
    j, i = divmod(int(cell), int(g["Ni"]))
    parts = []
    for name, h0, n, t0 in _pieces(domain, variable, start, end):
        r = _reader(f"data/{domain}/{variable}/{name}")
        v = np.asarray(r[j, i, h0:h0 + n], dtype=float)
        parts.append(pd.Series(v, index=pd.date_range(t0, periods=len(v), freq="h")))
    return pd.concat(parts) if parts else pd.Series(dtype=float)


# --- forecast runs: data_run/<domain>/YYYY/MM/DD/HHMMZ/<variable>.om, each
# [lat, lon, valid time] on the domain's grid, with meta.json listing the
# valid times (kept from 2026-08 on). Values at a valid time cover the
# interval since the previous one (mm for snowfall water equivalent).

READ_WORKERS = int(os.environ.get("PB_OM_WORKERS") or 32)  # point reads are network-bound
_local = threading.local()


def run_dir(domain: str, run: datetime) -> str:
    return f"data_run/{domain}/{run:%Y/%m/%d/%H%M}Z"


@lru_cache(maxsize=4096)
def run_meta(domain: str, run: datetime) -> dict | None:
    """The run's meta.json, or None when the run is not in the archive."""
    try:
        with _fs().open(f"{BUCKET}/{run_dir(domain, run)}/meta.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


@lru_cache(maxsize=4096)
def run_variables(domain: str, run: datetime) -> frozenset[str]:
    try:
        return frozenset(p.rsplit("/", 1)[-1][:-3] for p in _fs().ls(f"{BUCKET}/{run_dir(domain, run)}", detail=False)
                         if p.endswith(".om"))
    except FileNotFoundError:
        return frozenset()


def run_hours(domain: str, run: datetime) -> np.ndarray | None:
    """Valid times of the run as hours from its start."""
    m = run_meta(domain, run)
    if m is None:
        return None
    t = pd.to_datetime(m["valid_times"], utc=True)
    return np.asarray((t - pd.Timestamp(run)).total_seconds() / 3600.0)


def _thread_reader(path: str):
    cache = getattr(_local, "readers", None)
    if cache is None:
        cache = _local.readers = {}
    if path not in cache:
        if len(cache) > 32:
            cache.clear()
        cache[path] = _reader(path)
    return cache[path]


def run_values(domain: str, run: datetime, variable: str, cells: np.ndarray) -> np.ndarray | None:
    """(cells, valid times) of one variable; None when the file is missing."""
    g = grid(domain)[0]
    path = f"{run_dir(domain, run)}/{variable}.om"
    if not _fs().exists(f"{BUCKET}/{path}"):
        return None

    def one(cell):
        j, i = divmod(int(cell), int(g["Ni"]))
        return np.asarray(_thread_reader(path)[j, i, :], dtype=float)

    with ThreadPoolExecutor(READ_WORKERS) as pool:
        return np.vstack(list(pool.map(one, cells)))

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

Only regular latitude/longitude domains are handled here.
"""

from __future__ import annotations

import json
import logging
import re
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
    """(GRIB-style grid metadata for grib.terrain_index, terrain height m
    (flat), land fraction (flat: 1 where HSURF > -999))."""
    hsurf = np.asarray(_reader(f"data/{domain}/static/HSURF.om")[:, :], dtype=float)
    nj, ni = hsurf.shape
    bbox = re.search(r"BBOX\[([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\]", meta(domain)["crs_wkt"])
    if not bbox or "GEOGCRS" not in meta(domain)["crs_wkt"].split("[", 1)[0]:
        raise ValueError(f"{domain}: not a regular lat/lon domain")
    lat0, lon0, lat1, lon1 = (float(v) for v in bbox.groups())
    g = {
        "gridType": "regular_ll", "Ni": ni, "Nj": nj,
        "latitudeOfFirstGridPointInDegrees": lat0, "longitudeOfFirstGridPointInDegrees": lon0,
        "iDirectionIncrementInDegrees": (lon1 - lon0) / (ni - 1),
        "jDirectionIncrementInDegrees": (lat1 - lat0) / (nj - 1),
        "jScansPositively": 1, "iScansNegatively": 0, "jPointsAreConsecutive": 0,
    }
    land = (hsurf > -999.0).astype(float).ravel()
    height = np.where(hsurf > -999.0, hsurf, np.nan).ravel()
    return g, height, land


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

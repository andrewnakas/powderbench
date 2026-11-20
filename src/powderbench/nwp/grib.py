"""Byte-range GRIB access shared by the direct model sources.

Producers publish one GRIB file per model run and forecast step, with an index
that gives each field's byte offset. Reading one field means one HTTP range
request (~0.3-1.5 MB for a global 0.25 deg field), and that one message covers
every station on Earth.

Downloads are cached on disk (PB_NWP_CACHE, default a temp folder) for
CACHE_DAYS, so leagues that share a run, and a restarted hindcast, reuse them.
Needs the optional `eccodes` dependency (pip install -e .[grib]).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import tempfile
import time
from pathlib import Path

import numpy as np
import requests

log = logging.getLogger(__name__)

CACHE_DAYS = 3
ATTEMPTS = 5
TIMEOUT_S = 60
USER_AGENT = "powderbench (+https://powderbench.com)"
GRID_KEYS = (
    "gridType", "Ni", "Nj",
    "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
    "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
    "jScansPositively", "iScansNegatively", "jPointsAreConsecutive",
)

_session: requests.Session | None = None
_pruned: set[Path] = set()


def cache_dir() -> Path:
    root = Path(os.environ.get("PB_NWP_CACHE") or Path(tempfile.gettempdir()) / "powderbench-nwp")
    root.mkdir(parents=True, exist_ok=True)
    if root not in _pruned:  # once per process: drop downloads older than CACHE_DAYS
        _pruned.add(root)
        stale = time.time() - CACHE_DAYS * 86400
        for p in root.iterdir():
            try:
                if p.stat().st_mtime < stale:
                    p.unlink()
            except OSError:
                pass
    return root


def _http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def fetch(url: str, offset: int | None = None, length: int | None = None) -> bytes | None:
    """GET (optionally one byte range) with retries and the disk cache.
    Returns None when the object does not exist (yet)."""
    key = hashlib.sha1(f"{url}|{offset}|{length}".encode()).hexdigest()
    path = cache_dir() / key
    if path.exists():
        return path.read_bytes()
    headers = {}
    if offset is not None:
        end = "" if length is None else str(offset + length - 1)
        headers["Range"] = f"bytes={offset}-{end}"
    for attempt in range(ATTEMPTS):
        try:
            r = _http().get(url, headers=headers, timeout=TIMEOUT_S)
            if r.status_code in (403, 404):  # S3 answers 403 for a missing key on some buckets
                return None
            r.raise_for_status()
            data = r.content
            if length is not None and len(data) != length:
                raise OSError(f"short read: {len(data)} of {length} bytes")
            tmp = path.with_name(f"{key}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data
        except (requests.RequestException, OSError) as exc:
            if attempt == ATTEMPTS - 1:
                raise
            wait = round(3 * 2**attempt * random.uniform(0.7, 1.3), 1)  # jitter: parallel retries spread out
            log.warning("GRIB fetch failed (%s: %s), retry in %ss", url.rsplit("/", 1)[-1], exc, wait)
            time.sleep(wait)
    return None


def ecmwf_index(text: str) -> list[dict]:
    """ECMWF open-data .index: one JSON object per message."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            log.warning("bad ECMWF index line: %.80s", line)
    return out


_NCEP_TIME = re.compile(r"^(\d+)-(\d+) (hour|day) (acc|ave)")


def ncep_index(text: str) -> list[dict]:
    """NCEP .idx ("n:offset:d=YYYYMMDDHH:VAR:LEVEL:TIME:"). Each message's length
    is the gap to the next offset; the last one has length None (read to EOF).
    For accumulations and averages, start_h/end_h give the window in hours."""
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        row = {"var": parts[3], "level": parts[4], "time": parts[5], "offset": int(parts[1])}
        m = _NCEP_TIME.match(parts[5])
        if m:
            k = 24 if m.group(3) == "day" else 1
            row.update(start_h=int(m.group(1)) * k, end_h=int(m.group(2)) * k, stat=m.group(4))
        rows.append(row)
    rows.sort(key=lambda r: r["offset"])
    for a, b in zip(rows, rows[1:]):
        a["length"] = b["offset"] - a["offset"]
    if rows:
        rows[-1]["length"] = None
    return rows


def decode(message: bytes) -> tuple[np.ndarray, dict]:
    """One GRIB message -> (values, grid metadata). Missing points are NaN."""
    import eccodes

    gid = eccodes.codes_new_from_message(message)
    try:
        values = np.asarray(eccodes.codes_get_values(gid), dtype=float)
        if eccodes.codes_get(gid, "bitmapPresent"):
            values[values == eccodes.codes_get(gid, "missingValue")] = np.nan
        meta = {k: eccodes.codes_get(gid, k) for k in GRID_KEYS}
        meta["units"] = eccodes.codes_get(gid, "units")
    finally:
        eccodes.codes_release(gid)
    return values, meta


_MM_PER_UNIT = {"m": 1000.0, "m of water equivalent": 1000.0, "kg m**-2": 1.0, "kg m-2": 1.0, "kg/m^2": 1.0, "mm": 1.0}


def to_mm(units: str) -> float:
    """Factor from a GRIB water-equivalent unit to mm."""
    try:
        return _MM_PER_UNIT[units.strip()]
    except KeyError:
        raise ValueError(f"unknown water-equivalent unit {units!r}") from None


def point_index(meta: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Nearest grid cell of a regular lat/lon grid for each point (flat index
    into the message's values; -1 when the point is off the grid)."""
    if meta["gridType"] != "regular_ll" or meta["iScansNegatively"] or meta["jPointsAreConsecutive"]:
        raise ValueError(f"unsupported grid layout: {meta}")
    ni, nj = int(meta["Ni"]), int(meta["Nj"])
    di, dj = float(meta["iDirectionIncrementInDegrees"]), float(meta["jDirectionIncrementInDegrees"])
    lat0, lon0 = float(meta["latitudeOfFirstGridPointInDegrees"]), float(meta["longitudeOfFirstGridPointInDegrees"])
    dlat = (lats - lat0) if meta["jScansPositively"] else (lat0 - lats)
    j = np.rint(dlat / dj).astype(np.int64)
    i = np.rint(((lons - lon0) % 360.0) / di).astype(np.int64)
    if ni * di >= 359.9:  # global: longitudes wrap
        i %= ni
    ok = (j >= 0) & (j < nj) & (i >= 0) & (i < ni)
    return np.where(ok, j * ni + i, -1)


def at_points(values: np.ndarray, idx: np.ndarray) -> np.ndarray:
    out = np.full(len(idx), np.nan)
    ok = idx >= 0
    out[ok] = values[idx[ok]]
    return out

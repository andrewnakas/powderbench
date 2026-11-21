"""ERA5 daily snowfall without Open-Meteo's API.

Why: the API bills about one call per location per 14 days of data, so a
climatology (11 stations x 35 years ~ 10k calls) is a whole day's free budget
for one IP. Two sources with no per-call budget (PB_ERA5_SOURCE):

  s3   Open-Meteo's own open-data archive (nwp/omdata.py): the hourly ERA5
       the API serves for era5_seamless, read in seconds per station-year.
  cds  the Copernicus Climate Data Store, ERA5 (0.25 deg) or ERA5-Land
       (0.1 deg); queued requests, needs ~/.cdsapirc (free account). A
       fallback should the archive go away.

Processing follows the API's daily snowfall_sum for era5_seamless, which a
check against the era5 league's published truth pinned down (118 rounds,
2026-06-22..10-31, scripts/compare_era5_direct.py): the model's snowfall
water equivalent at Open-Meteo's cell (grib.terrain_index on the model's own
terrain) x 0.7 cm per mm, summed over station-local days. Unlike the forecast
models (nwp/sources.py), the API applies no elevation rule to ERA5: with it
the totals came out 15% low (Antillanca 71% low), without it within 3%.
Days here follow the station zone's DST; the API holds one UTC offset for a
whole request, which accounts for most of the remaining day-to-day mismatch.

CDS products: "era5" (`reanalysis-era5-single-levels`, values are the hour
ending at the stamp) and "era5-land" (`reanalysis-era5-land`, values
accumulate from 00 UTC, the 00 UTC value closing the previous day). Stations
close together share one request per year; downloads go to the cache
(PB_ERA5_CACHE, default data/cache/era5) and are reduced to each station's
own series, so a long build resumes and the GRIB files do not pile up.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..stations import Station, data_dir
from . import CM_PER_INCH, CM_PER_MM_WE, station_zone
from . import grib
from .sources import G0

log = logging.getLogger(__name__)

S3_DOMAIN = "copernicus_era5"


@dataclass(frozen=True)
class Product:
    name: str
    dataset: str
    step_deg: float
    running_from_00z: bool  # ERA5-Land: accumulations run from 00 UTC


PRODUCTS = {
    "era5": Product("era5", "reanalysis-era5-single-levels", 0.25, False),
    "era5-land": Product("era5-land", "reanalysis-era5-land", 0.1, True),
}
PAD_DEG = 0.5  # around each cluster: the terrain search reaches past the nearest cell
CLUSTER_SPAN_DEG = 2.0  # stations within this lat/lon span share a request
STATIC_YEAR = 2000  # orography and land mask do not change with time
GLOBE = [90.0, -180.0, -90.0, 180.0]
# Requests sent to the CDS at once: its queue can hold one request for hours
# (2026-11-21: about 3.5 h each), so clusters go in parallel
CDS_PARALLEL = int(os.environ.get("PB_CDS_PARALLEL") or 6)


def default_source() -> str:
    """PB_ERA5_SOURCE when it names a direct source, else the archive."""
    src = os.environ.get("PB_ERA5_SOURCE", "")
    return src if src in ("s3", "cds") else "s3"


def cache_dir() -> Path:
    root = Path(os.environ.get("PB_ERA5_CACHE") or data_dir() / "cache" / "era5")
    root.mkdir(parents=True, exist_ok=True)
    return root


def clusters(stations: list[Station]) -> list[list[Station]]:
    """Greedy groups whose lat and lon spans stay within CLUSTER_SPAN_DEG."""
    out: list[list[Station]] = []
    for st in sorted(stations, key=lambda s: (s.longitude, s.latitude)):
        for grp in out:
            lats = [s.latitude for s in grp] + [st.latitude]
            lons = [s.longitude for s in grp] + [st.longitude]
            if max(lats) - min(lats) <= CLUSTER_SPAN_DEG and max(lons) - min(lons) <= CLUSTER_SPAN_DEG:
                grp.append(st)
                break
        else:
            out.append([st])
    return out


def area(group: list[Station], product: Product) -> list[float]:
    """[N, W, S, E] snapped outward to the product's grid."""
    q = product.step_deg
    up = lambda v: float(np.ceil(round(v / q, 6)) * q)  # noqa: E731
    down = lambda v: float(np.floor(round(v / q, 6)) * q)  # noqa: E731
    return [round(up(max(s.latitude for s in group) + PAD_DEG), 4),
            round(down(min(s.longitude for s in group) - PAD_DEG), 4),
            round(down(min(s.latitude for s in group) - PAD_DEG), 4),
            round(up(max(s.longitude for s in group) + PAD_DEG), 4)]


def request(product: Product, variables: list[str], year: int, months: list[int], box: list[float],
            hours: list[int] | None = None, days: list[int] | None = None) -> dict:
    req = {
        "variable": variables,
        "year": [str(year)],
        "month": [f"{m:02d}" for m in months],
        "day": [f"{d:02d}" for d in (days or range(1, 32))],  # the CDS skips 31 Feb etc.
        "time": [f"{h:02d}:00" for h in (range(24) if hours is None else hours)],
        "area": box,
        "data_format": "grib",
        "download_format": "unarchived",
    }
    if product.name == "era5":
        req["product_type"] = ["reanalysis"]
    return req


def _key(product: Product, req: dict) -> str:
    return hashlib.sha256(json.dumps([product.dataset, req], sort_keys=True).encode()).hexdigest()[:20]


def retrieve(product: Product, req: dict) -> Path:
    """The request's GRIB file (downloaded once; _reduce removes it after use)."""
    out = cache_dir() / f"{product.name}-{_key(product, req)}.grib"
    if out.exists() and out.stat().st_size > 0:
        return out
    import cdsapi  # optional dependency (pip install "powderbench[era5]")

    client = cdsapi.Client(quiet=True, progress=False)
    tmp = out.with_suffix(".part")
    log.info("CDS %s %s %s %s", product.dataset, req["variable"], req["year"], req["area"])
    client.retrieve(product.dataset, req, str(tmp))
    if zipfile.is_zipfile(tmp):  # ERA5-Land answers a mixed request with one GRIB per stream
        with zipfile.ZipFile(tmp) as z, open(out.with_suffix(".cat"), "wb") as f:
            for member in sorted(z.namelist()):
                f.write(z.read(member))
        tmp.unlink()
        out.with_suffix(".cat").replace(out)
    else:
        tmp.replace(out)
    return out


def messages(path: Path):
    """(shortName, valid time UTC, values, grid metadata) per GRIB message."""
    import eccodes

    with open(path, "rb") as f:
        while (gid := eccodes.codes_grib_new_from_file(f)) is not None:
            try:
                name = eccodes.codes_get(gid, "shortName")
                vd, vt = eccodes.codes_get(gid, "validityDate"), eccodes.codes_get(gid, "validityTime")
                msg = eccodes.codes_get_message(gid)
            finally:
                eccodes.codes_release(gid)
            valid = datetime.strptime(f"{vd}{vt:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            values, meta = grib.decode(msg)
            yield name, valid, values, meta


@lru_cache(maxsize=2)
def _static(name: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """(terrain height m, land fraction, grid) of the whole globe: one
    request per product, kept in the cache for good."""
    product = PRODUCTS[name]
    req = request(product, ["geopotential", "land_sea_mask"], STATIC_YEAR, [1], GLOBE, hours=[0], days=[1])
    static = {n: (values, meta) for n, _, values, meta in messages(retrieve(product, req))}
    (z, meta), (lsm, _) = static["z"], static["lsm"]
    return z / G0, lsm, meta


def cell_latlon(meta: dict, idx: int) -> tuple[float, float]:
    """Latitude and longitude of a regular lat/lon grid's cell (flat index)."""
    j, i = divmod(int(idx), int(meta["Ni"]))
    dj, di = float(meta["jDirectionIncrementInDegrees"]), float(meta["iDirectionIncrementInDegrees"])
    lat0, lon0 = float(meta["latitudeOfFirstGridPointInDegrees"]), float(meta["longitudeOfFirstGridPointInDegrees"])
    lat = lat0 + j * dj if meta["jScansPositively"] else lat0 - j * dj
    return round(lat, 4), round((lon0 + i * di + 180.0) % 360.0 - 180.0, 4)


def cells(group: list[Station], product: Product) -> dict[str, tuple[float, float]]:
    """station_id -> (lat, lon) of its cell: Open-Meteo's choice on the
    product's own orography and land mask (stations with no cell left out)."""
    height, land, meta = _static(product.name)
    idx = grib.terrain_index(meta, np.array([s.latitude for s in group]), np.array([s.longitude for s in group]),
                             np.array([s.elevation_ft * 0.3048 for s in group]), height, land, radius=1)
    return {s.station_id: cell_latlon(meta, i) for s, i in zip(group, idx) if i >= 0}


def _reduce(product: Product, group: list[Station], at: dict[str, int], year: int,
            months: list[int]) -> dict[str, pd.Series]:
    """Snowfall (mm water, the product's accumulation convention) at each
    station's cell for one year, cached per station; the GRIB is removed once
    every station in the cluster has its series."""
    req = request(product, ["snowfall"], year, months, area(group, product))
    key = _key(product, req)
    paths = {s.station_id: cache_dir() / f"{product.name}-{key}-{s.station_id.replace(':', '_')}.csv.gz"
             for s in group}
    if all(p.exists() for p in paths.values()):
        return {sid: pd.read_csv(p, index_col=0, parse_dates=[0])["sf"] for sid, p in paths.items()}
    grib_path = retrieve(product, req)
    sf: dict[str, dict] = {s.station_id: {} for s in group}
    sids, box = list(at), None
    for name, valid, values, meta in messages(grib_path):
        if name == "sf":
            if box is None:  # the cells' places in this request's box
                box = grib.point_index(meta, np.array([at[s][0] for s in sids]), np.array([at[s][1] for s in sids]))
            for sid, cell in zip(sids, box):
                if cell >= 0:
                    sf[sid][valid] = float(values[cell]) * grib.to_mm(meta["units"])
    out = {}
    for s in group:
        ser = pd.Series(sf[s.station_id], dtype=float, name="sf").sort_index()
        ser.index.name = "time"
        ser.to_frame().to_csv(paths[s.station_id])
        out[s.station_id] = ser
    grib_path.unlink()
    return out


def hourly_snow_mm(sf: pd.Series, product: Product) -> pd.Series:
    """Water (mm) that fell in the hour ending at each stamp, from the
    product's accumulation convention."""
    if not product.running_from_00z:
        return sf.clip(lower=0)
    # ERA5-Land: running total since 00 UTC; the 00 UTC value is the previous
    # day's total, so the hour ending 01 UTC is the 01 UTC value itself
    first_of_day = sf.index.hour == 1
    inc = np.where(first_of_day, sf, sf - sf.shift(1))
    gap = sf.index.to_series().diff() != pd.Timedelta(hours=1)
    inc = np.where(gap & ~first_of_day, np.nan, inc)
    return pd.Series(inc, index=sf.index).clip(lower=0)


def local_daily(snow: pd.Series, zone, begin: date, end: date) -> pd.Series:
    """Sums over station-local days, the accumulation interpolated to local
    midnight (exact on whole-hour offsets). Days not fully covered are left out."""
    snow = snow.dropna()
    if snow.empty:
        return pd.Series(dtype=float)
    t = snow.index.map(lambda ts: ts.timestamp()).to_numpy(float)
    tc = np.concatenate([[t[0] - 3600.0], t])  # each value is the hour ending at its stamp
    cum = np.concatenate([[0.0], np.cumsum(snow.to_numpy())])
    out = {}
    d = begin
    while d <= end:
        lo = datetime(d.year, d.month, d.day, tzinfo=zone)
        lo_s, hi_s = lo.timestamp(), (lo + timedelta(days=1)).astimezone(timezone.utc).timestamp()
        if lo_s >= tc[0] and hi_s <= tc[-1]:
            out[d] = float(np.interp(hi_s, tc, cum) - np.interp(lo_s, tc, cum))
        d += timedelta(days=1)
    return pd.Series(out, dtype=float)


def _s3_hourly(stations: list[Station], first: date, last: date):
    """(station, hourly snow water mm) from the open-data archive."""
    from . import omdata

    g, height, land = omdata.grid(S3_DOMAIN)
    idx = grib.terrain_index(g, np.array([s.latitude for s in stations]), np.array([s.longitude for s in stations]),
                             np.array([s.elevation_ft * 0.3048 for s in stations]), height, land, radius=1)
    t0 = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    t1 = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
    for st, cell in zip(stations, idx):
        if cell >= 0:
            yield st, omdata.series(S3_DOMAIN, "snowfall_water_equivalent", cell, t0, t1).clip(lower=0)


def _cds_hourly(stations: list[Station], first: date, last: date, product: Product):
    """(station, hourly snow water mm) from the Climate Data Store: one
    request per cluster and year, CDS_PARALLEL at a time."""
    groups = clusters(stations)
    at = {sid: ll for g in groups for sid, ll in cells(g, product).items()}
    years = [(y, list(range(first.month if y == first.year else 1, (last.month if y == last.year else 12) + 1)))
             for y in range(first.year, last.year + 1)]
    with ThreadPoolExecutor(CDS_PARALLEL) as pool:
        done = {(k, y): pool.submit(_reduce, product, g, {s.station_id: at[s.station_id] for s in g
                                                         if s.station_id in at}, y, months)
                for k, g in enumerate(groups) for y, months in years}
        got = {key: fut.result() for key, fut in done.items()}
    for k, group in enumerate(groups):
        for st in group:
            parts = [got[(k, y)][st.station_id] for y, _ in years if len(got[(k, y)].get(st.station_id, ()))]
            if not parts:
                continue
            sf = pd.concat(parts).sort_index()
            yield st, hourly_snow_mm(sf[~sf.index.duplicated()], product)


def daily_snowfall(stations: list[Station], begin: date, end: date, product: str = "era5",
                   source: str | None = None) -> pd.DataFrame:
    """Same contract as openmeteo.era5_daily_snowfall: station_id, date,
    snowfall_in (station-local days)."""
    src = source or default_source()
    first, last = begin - timedelta(days=1), end + timedelta(days=1)
    if src == "s3":
        if product != "era5":
            raise ValueError("the open-data archive has ERA5 snowfall only (not ERA5-Land)")
        hourly = _s3_hourly(stations, first, last)
    elif src == "cds":
        hourly = _cds_hourly(stations, first, last, PRODUCTS[product])
    else:
        raise ValueError(f"unknown ERA5 source {src!r} (s3 or cds)")
    rows = []
    for st, snow in hourly:
        daily = local_daily(snow, station_zone(st.latitude, st.longitude), begin, end)
        rows += [{"station_id": st.station_id, "date": d, "snowfall_in": mm * CM_PER_MM_WE / CM_PER_INCH}
                 for d, mm in daily.items()]
    return pd.DataFrame(rows, columns=["station_id", "date", "snowfall_in"])

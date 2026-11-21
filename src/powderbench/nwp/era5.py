"""ERA5 daily snowfall read from the Copernicus Climate Data Store (CDS),
the archive Open-Meteo's `era5_seamless` resamples.

Why: Open-Meteo bills about one call per location per 14 days of data, so a
climatology (11 stations x 35 years ~ 10k calls) is a whole day's free budget
for one IP, and a request that size is refused outright. The CDS has no
per-call budget: requests are queued and served whole.

What it reproduces per station (the conventions of the direct NWP readers in
nwp/__init__.py, so values line up with Open-Meteo's daily snowfall_sum):
  - hourly `snowfall` (water equivalent) and `2m_temperature` on the model grid;
  - Open-Meteo's cell choice (grib.terrain_index: a nearby land cell whose
    model height best matches the station) on the model's own orography and
    land mask;
  - Open-Meteo's elevation rule (sources.below_zero_share): where that cell is
    more than 100 m above or below the station, an hour's snow is kept only for
    the share of it the temperature moved to the station's height is below 0 C;
  - 0.7 cm of snow per mm of water, station-local days (IANA zone), the
    accumulation interpolated to local midnight.

Two products: "era5" (0.25 deg, `reanalysis-era5-single-levels`, values are
the hour ending at the stamp) and "era5-land" (0.1 deg, `reanalysis-era5-land`,
values accumulate from 00 UTC, the 00 UTC value closing the previous day).

Stations close together share one request (one box per cluster and year).
Downloads go to the cache (PB_ERA5_CACHE, default data/cache/era5) and are
reduced to each station's own cell series there, so a long build resumes
and the multi-MB GRIB files do not pile up.

Credentials: ~/.cdsapirc or CDSAPI_URL / CDSAPI_KEY (a free CDS account;
each dataset's licence is accepted once on its CDS page).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..stations import Station, data_dir
from . import CM_PER_INCH, CM_PER_MM_WE, station_zone
from . import grib
from .sources import G0, LAPSE_K_PER_M, RULE_MIN_DZ_M, below_zero_share

log = logging.getLogger(__name__)


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
    """The request's GRIB file (downloaded once; see _reduce for cleanup)."""
    out = cache_dir() / f"{product.name}-{_key(product, req)}.grib"
    if out.exists() and out.stat().st_size > 0:
        return out
    import cdsapi  # optional dependency (pip install cdsapi)

    client = cdsapi.Client(quiet=True, progress=False)
    tmp = out.with_suffix(".part")
    log.info("CDS %s %s %s %s", product.dataset, req["variable"], req["year"], req["area"])
    client.retrieve(product.dataset, req, str(tmp))
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


def cells(group: list[Station], product: Product) -> dict[str, tuple[int, float]]:
    """station_id -> (cell index in the cluster box, model terrain height m)."""
    req = request(product, ["geopotential", "land_sea_mask"], STATIC_YEAR, [1], area(group, product),
                  hours=[0], days=[1])
    static = {name: (values, meta) for name, _, values, meta in messages(retrieve(product, req))}
    (z, meta), (lsm, _) = static["z"], static["lsm"]
    height = z / G0
    lats = np.array([s.latitude for s in group])
    lons = np.array([s.longitude for s in group])
    elev = np.array([s.elevation_ft * 0.3048 for s in group])
    idx = grib.terrain_index(meta, lats, lons, elev, height, lsm, radius=1)
    return {s.station_id: (int(i), float(height[i])) for s, i in zip(group, idx)}


def _reduce(product: Product, group: list[Station], at: dict[str, tuple[int, float]],
            year: int, months: list[int]) -> dict[str, pd.DataFrame]:
    """Hourly sf (mm water, raw accumulation convention) and t2 (C) at each
    station's cell for one year, cached per station; the GRIB is removed once
    every station in the cluster has its series."""
    req = request(product, ["snowfall", "2m_temperature"], year, months, area(group, product))
    key = _key(product, req)
    paths = {s.station_id: cache_dir() / f"{product.name}-{key}-{s.station_id.replace(':', '_')}.csv.gz"
             for s in group}
    if all(p.exists() for p in paths.values()):
        return {sid: pd.read_csv(p, index_col=0, parse_dates=[0]) for sid, p in paths.items()}
    grib_path = retrieve(product, req)
    sf: dict[str, dict] = {s.station_id: {} for s in group}
    t2: dict[str, dict] = {s.station_id: {} for s in group}
    for name, valid, values, meta in messages(grib_path):
        factor = grib.to_mm(meta["units"]) if name == "sf" else None
        for sid, (cell, _) in at.items():
            v = float(values[cell])
            if name == "sf":
                sf[sid][valid] = v * factor
            elif name == "2t":
                t2[sid][valid] = v - 273.15
    out = {}
    for s in group:
        df = pd.DataFrame({"sf": pd.Series(sf[s.station_id], dtype=float),
                           "t2": pd.Series(t2[s.station_id], dtype=float)}).sort_index()
        df.index.name = "time"
        df.to_csv(paths[s.station_id])
        out[s.station_id] = df
    grib_path.unlink()
    return out


def hourly_snow_mm(raw: pd.DataFrame, product: Product) -> pd.Series:
    """Water (mm) that fell in the hour ending at each stamp, from the
    product's accumulation convention."""
    sf = raw["sf"]
    if not product.running_from_00z:
        return sf.clip(lower=0)
    # ERA5-Land: running total since 00 UTC; the 00 UTC value is the previous
    # day's total, so the hour ending 01 UTC is the 01 UTC value itself
    prev = sf.shift(1)
    first_of_day = sf.index.hour == 1
    inc = np.where(first_of_day, sf, sf - prev)
    gap = sf.index.to_series().diff() != pd.Timedelta(hours=1)
    inc = np.where(gap & ~first_of_day, np.nan, inc)
    return pd.Series(inc, index=sf.index).clip(lower=0)


def apply_rule(snow_mm: pd.Series, t2_c: pd.Series, dz_m: float) -> pd.Series:
    """Open-Meteo's elevation rule on hourly snow. dz_m: model cell height
    minus the station's. The hour's start temperature is the previous stamp's."""
    if abs(dz_m) <= RULE_MIN_DZ_M:
        return snow_mm
    shift = dz_m * LAPSE_K_PER_M
    t_end = t2_c.to_numpy() + shift
    t_start = np.concatenate([[t_end[0]], t_end[:-1]])
    return snow_mm * below_zero_share(t_start, t_end)


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
        hi = lo + timedelta(days=1)
        lo_s, hi_s = lo.timestamp(), hi.astimezone(timezone.utc).timestamp()
        if lo_s >= tc[0] and hi_s <= tc[-1]:
            out[d] = float(np.interp(hi_s, tc, cum) - np.interp(lo_s, tc, cum))
        d += timedelta(days=1)
    return pd.Series(out, dtype=float)


def daily_snowfall(stations: list[Station], begin: date, end: date, product: str = "era5") -> pd.DataFrame:
    """Same contract as openmeteo.era5_daily_snowfall: station_id, date,
    snowfall_in (station-local days)."""
    prod = PRODUCTS[product]
    first, last = begin - timedelta(days=1), end + timedelta(days=1)
    rows = []
    for group in clusters(stations):
        at = cells(group, prod)
        series: dict[str, list[pd.DataFrame]] = {s.station_id: [] for s in group}
        for year in range(first.year, last.year + 1):
            m0 = first.month if year == first.year else 1
            m1 = last.month if year == last.year else 12
            for sid, df in _reduce(prod, group, at, year, list(range(m0, m1 + 1))).items():
                series[sid].append(df)
        for st in group:
            raw = pd.concat(series[st.station_id]).sort_index()
            raw = raw[~raw.index.duplicated()]
            snow = apply_rule(hourly_snow_mm(raw, prod), raw["t2"],
                              at[st.station_id][1] - st.elevation_ft * 0.3048)
            daily = local_daily(snow, station_zone(st.latitude, st.longitude), begin, end)
            rows += [{"station_id": st.station_id, "date": d,
                      "snowfall_in": mm * CM_PER_MM_WE / CM_PER_INCH} for d, mm in daily.items()]
    return pd.DataFrame(rows, columns=["station_id", "date", "snowfall_in"])

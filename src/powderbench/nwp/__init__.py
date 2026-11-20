"""Model forecasts read from the producers' own files ("direct" NWP).

Open-Meteo resamples the same public model output, but its free tier bills
every station location against a per-IP budget (10k calls a day), which is why
most model baselines ran on featured stations only, and why a busy day could
stall a hindcast for hours. The producers publish the raw files on free object
storage with byte-range indexes, so one ~0.4 MB message covers every station:

  ecmwf_ifs   ECMWF IFS 0.25 deg     s3://ecmwf-forecasts      sf, 3-hourly   CC BY 4.0
  ecmwf_aifs  ECMWF AIFS single      s3://ecmwf-forecasts      sf, 6-hourly   CC BY 4.0
  gfs         NOAA GFS 0.25 deg      s3://noaa-gfs-bdp-pds     APCP x CSNOW   public domain

Daily totals follow Open-Meteo's conventions so baselines stay comparable
across the switch: station-local days (IANA zone from the coordinates, DST
aware, as timezone=auto), snow depth = 0.7 cm per mm of snowfall water
equivalent (Open-Meteo's 7:1), the grid cell chosen like Open-Meteo's default
(a nearby land cell whose model terrain height best matches the station's),
accumulations interpolated linearly to the local-midnight boundaries between
forecast steps.

Run choice is honest. Live: the newest run whose files are online now (and
never one issued after the round's cutoff). Hindcast: the newest run that was
fully online at the baselines slot, using the measured publish delays. That is
stricter than Open-Meteo's historical-forecast archive, which stitches each
run's first hours together, so direct hindcast skill is not optimistic at 48/72 h.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .sources import SOURCES, Source

log = logging.getLogger(__name__)

CM_PER_MM_WE = 0.7  # Open-Meteo's snowfall convention: 7 cm of snow per 10 mm of water
CM_PER_INCH = 2.54
LIVE_MIN_AGE_H = 3.0  # no producer has a run online sooner than this after its cycle
MAX_RUNS_BACK = 6


@lru_cache(maxsize=None)
def station_zone(lat: float, lon: float):
    """IANA zone at the coordinates; a solar offset if tzfpy is missing."""
    try:
        import tzfpy

        name = tzfpy.get_tz(lon, lat)
        if name:
            return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - optional dependency or unknown zone
        log.debug("no IANA zone for %s,%s", lat, lon, exc_info=True)
    return timezone(timedelta(hours=round(lon / 15)))


def local_midnight_utc(zone, d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=zone).astimezone(timezone.utc)


def candidate_runs(source: Source, as_of: datetime, live: bool) -> list[datetime]:
    """Newest first: cycles old enough to be online at as_of."""
    t = as_of - timedelta(hours=LIVE_MIN_AGE_H if live else source.online_h)
    day = datetime(t.year, t.month, t.day, tzinfo=timezone.utc)
    runs = []
    while len(runs) < MAX_RUNS_BACK:
        for h in sorted(source.cycles, reverse=True):
            run = day + timedelta(hours=h)
            if run <= t and len(runs) < MAX_RUNS_BACK:
                runs.append(run)
        day -= timedelta(days=1)
    return runs


def _boundaries(stations, begin: date, end: date, run: datetime) -> np.ndarray:
    """(stations, days + 1) hours from the run start to each local midnight."""
    days = [begin + timedelta(days=k) for k in range((end - begin).days + 2)]
    return np.array([[(local_midnight_utc(station_zone(s.latitude, s.longitude), d) - run).total_seconds() / 3600
                      for d in days] for s in stations])


def _needed_steps(bounds: np.ndarray, steps: np.ndarray) -> list[int]:
    need = set()
    for b in bounds.ravel():
        if b < 0 or b > steps[-1]:
            continue
        i = int(np.searchsorted(steps, b))
        need.add(int(steps[i]))
        if steps[i] != b:
            need.add(int(steps[i - 1]))
    return sorted(need)


def daily_from_run(source: Source, run: datetime, stations, begin: date, end: date) -> pd.DataFrame | None:
    """station_id, date, snowfall_in (station-local days) from one run, or
    None when the run is not on the server or is incomplete."""
    avail = source.steps(run)
    if avail is None:
        return None
    avail = np.asarray(avail, dtype=float)
    bounds = _boundaries(stations, begin, end, run)
    need = _needed_steps(bounds, avail)
    if not need:
        return None
    lats = np.array([s.latitude for s in stations])
    lons = np.array([s.longitude for s in stations])
    elev = np.array([s.elevation_ft * 0.3048 for s in stations], dtype=float)
    acc = source.accum(run, need, lats, lons, elev)
    if acc is None:
        return None
    xs = np.array(need, dtype=float)
    table = np.vstack([acc[s] for s in need])  # (steps, stations)
    days = [begin + timedelta(days=k) for k in range((end - begin).days + 1)]
    rows = []
    for j, st in enumerate(stations):
        b = bounds[j]
        inside = (b >= 0) & (b <= avail[-1])
        a = np.interp(b, xs, table[:, j])
        for k, d in enumerate(days):
            mm = a[k + 1] - a[k]
            ok = inside[k] and inside[k + 1] and np.isfinite(mm)
            rows.append({"station_id": st.station_id, "date": d,
                         "snowfall_in": round(max(mm, 0.0) * CM_PER_MM_WE / CM_PER_INCH, 4) if ok else None})
    df = pd.DataFrame(rows)
    df.attrs.update(source=source.name, run=run.isoformat())
    return df


def covers(source: Source, run: datetime, stations, begin: date, end: date) -> bool | None:
    """True when the run reaches every station's last local midnight (no
    downloads beyond the step list); None when the run is not online."""
    avail = source.steps(run)
    if avail is None:
        return None
    bounds = _boundaries(stations, begin, end, run)
    return bool(bounds.min() >= 0 and bounds.max() <= avail[-1])


def daily_snowfall(stations, begin: date, end: date, model: str, as_of: datetime, live: bool) -> pd.DataFrame:
    """Daily snowfall (inches) per station-local day in [begin, end] from the
    newest suitable run of `model` (a key of SOURCES). Prefers a run that
    reaches every day; falls back to the newest partial one."""
    source = SOURCES[model]
    stations = list(stations)
    if not stations:
        return pd.DataFrame(columns=["station_id", "date", "snowfall_in"])
    partial = []
    for run in candidate_runs(source, as_of, live):
        full = covers(source, run, stations, begin, end)
        if full is None:
            continue
        if not full:
            partial.append(run)
            continue
        df = daily_from_run(source, run, stations, begin, end)
        if df is not None:
            return df
        log.info("%s run %s incomplete; trying an older one", model, run)
    for run in partial:
        df = daily_from_run(source, run, stations, begin, end)
        if df is not None:
            return df
    raise LookupError(f"no {model} run online for {begin}..{end} as of {as_of:%Y-%m-%d %H:%MZ}")

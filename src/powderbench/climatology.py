"""Per-league snowfall climatology: per-station, per-day-of-year stats built
from that league's truth history (SNOTEL for stations, ERA5 for era5).
Used as the no-skill reference for the Powder Score and as a baseline.

The point forecast is the climatological *median* (MAE-optimal for a
no-information forecaster), not the mean — under MAE the mean is trivially
beatable on dry days, which would inflate everyone's skill.
"""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

import pandas as pd

from . import HORIZONS, POWDER_ALERT_INCHES, QUANTILES
from .leagues import League, get_league
from .scoring import QUANTILE_COLS
from .stations import data_dir, station_ids

log = logging.getLogger(__name__)

WINDOW_DAYS = 7  # pool doy +/- 7 across years
MIN_SAMPLES = 30


def _window_sums(daily: pd.DataFrame) -> pd.DataFrame:
    """Per station-day cumulative sums for each horizon (NaN unless every
    component day is valid)."""
    frames = []
    for station_id, grp in daily.groupby("station_id"):
        grp = grp.set_index("date").sort_index()
        s24 = grp["snow24"].where(grp["valid"])
        out = pd.DataFrame(index=grp.index)
        out["station_id"] = station_id
        for h in HORIZONS:
            n = h // 24
            # sum of the next n days starting at d (min_periods=n -> NaN on any gap)
            out[f"h{h}"] = s24[::-1].rolling(n, min_periods=n).sum()[::-1]
        frames.append(out.reset_index())
    return pd.concat(frames, ignore_index=True)


CLIMO_BATCH = 200


def _climatology_rows(daily: pd.DataFrame) -> list[dict]:
    if not len(daily):
        return []
    sums = _window_sums(daily)
    sums["doy"] = pd.to_datetime(sums["date"]).dt.dayofyear.clip(upper=365)

    rows = []
    for station_id, grp in sums.groupby("station_id"):
        for doy in range(1, 366):
            window = (grp["doy"] - doy + 182) % 365 - 182  # circular distance
            pool = grp[window.abs() <= WINDOW_DAYS]
            row = {"station_id": station_id, "doy": doy}
            for h in HORIZONS:
                vals = pool[f"h{h}"].dropna()
                row[f"h{h}_n"] = len(vals)
                if len(vals) < MIN_SAMPLES:
                    continue
                row[f"h{h}_mean"] = round(float(vals.mean()), 3)
                for q in QUANTILES:
                    row[f"h{h}_{QUANTILE_COLS[q]}"] = round(float(vals.quantile(q)), 3)
                if h == 24:
                    row["h24_p6freq"] = round(float((vals >= POWDER_ALERT_INCHES).mean()), 4)
            rows.append(row)
    return rows


def build_climatology(league: League, begin: date, end: date, only_missing: bool = False) -> pd.DataFrame:
    """Fetch the league's truth history in [begin, end] and compute per-station
    day-of-year stats per horizon. Writes data/climatology/<league>.csv.gz.
    only_missing: compute just the stations without rows yet and merge them in
    (onboarding a new network without rebuilding everything)."""
    from .truth_sources import daily_truth

    ids = station_ids(league.name)
    existing = None
    if only_missing and (data_dir() / league.climatology_path).exists():
        existing = load_climatology(league)
        have = set(existing["station_id"])
        ids = [i for i in ids if i not in have]
    log.info("[%s] fetching %d stations %s..%s", league.name, len(ids), begin, end)
    rows = []
    # station batches bound memory: ~1k stations x 10 years is millions of rows
    for i in range(0, len(ids), CLIMO_BATCH):
        batch = ids[i : i + CLIMO_BATCH]
        log.info("[%s] stations %d-%d of %d", league.name, i + 1, i + len(batch), len(ids))
        rows += _climatology_rows(daily_truth(league, batch, begin, end))
    climo = pd.DataFrame(rows)
    if existing is not None:
        climo = pd.concat([existing, climo], ignore_index=True)
    out = data_dir() / f"climatology/{league.name}.csv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    climo.to_csv(out, index=False)
    (data_dir() / f"climatology/{league.name}.csv").unlink(missing_ok=True)
    _read_climatology.cache_clear()
    log.info("wrote %s (%d rows)", out, len(climo))
    return climo


def load_climatology(league: League | str) -> pd.DataFrame:
    league = get_league(league) if isinstance(league, str) else league
    return _read_climatology(str(data_dir() / league.climatology_path))


@lru_cache(maxsize=8)
def _read_climatology(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def climatology_prediction(target_day: date, league: League | str, climo: pd.DataFrame | None = None) -> pd.DataFrame:
    """Climatology baseline submission for a round: median point forecast,
    full quantiles, and empirical powder-day probability."""
    climo = load_climatology(league) if climo is None else climo
    doy = min(target_day.timetuple().tm_yday, 365)
    day = climo[climo["doy"] == doy]
    frames = []
    for h in HORIZONS:
        if f"h{h}_mean" not in day.columns:
            continue
        sub = day[day[f"h{h}_mean"].notna()]
        frame = pd.DataFrame({"station_id": sub["station_id"].values, "horizon_h": h,
                              "snowfall_in": sub[f"h{h}_p50"].values})
        for col in QUANTILE_COLS.values():
            frame[col] = sub[f"h{h}_{col}"].values
        if h == 24:
            frame["prob_6in"] = sub["h24_p6freq"].values
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["station_id", "horizon_h", "snowfall_in"])
    out = pd.concat(frames, ignore_index=True)
    order = {sid: i for i, sid in enumerate(day["station_id"])}
    out["_o"] = out["station_id"].map(order)
    return out.sort_values(["_o", "horizon_h"]).drop(columns="_o").reset_index(drop=True)

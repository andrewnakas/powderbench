"""Station snowfall climatology: per-station, per-day-of-year stats built from
SNOTEL history. Used as the no-skill reference for the Powder Score and as a
baseline competitor.

The point forecast is the climatological *median* (MAE-optimal for a
no-information forecaster), not the mean — under MAE the mean is trivially
beatable on dry days, which would inflate everyone's skill.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from . import HORIZONS, POWDER_ALERT_INCHES, QUANTILES
from .scoring import QUANTILE_COLS
from .stations import data_dir, station_ids
from . import snotel, truth

log = logging.getLogger(__name__)

WINDOW_DAYS = 7  # pool doy +/- 7 across years
CLIMO_PATH = "climatology/climatology.csv"


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


def build_climatology(begin: date, end: date) -> pd.DataFrame:
    """Fetch SNOTEL history in [begin, end], QC it, and compute per-station
    day-of-year stats for each horizon. Writes data/climatology/climatology.csv."""
    ids = station_ids()
    log.info("fetching %d stations %s..%s", len(ids), begin, end)
    obs = snotel.fetch_daily(ids, begin, end)
    daily = truth.daily_snowfall(obs)
    sums = _window_sums(daily)
    sums["doy"] = pd.to_datetime(sums["date"]).dt.dayofyear.clip(upper=365)

    rows = []
    for station_id, grp in sums.groupby("station_id"):
        for doy in range(1, 366):
            lo, hi = doy - WINDOW_DAYS, doy + WINDOW_DAYS
            window = (grp["doy"] - doy + 182) % 365 - 182  # circular distance
            pool = grp[window.abs() <= WINDOW_DAYS]
            row = {"station_id": station_id, "doy": doy}
            for h in HORIZONS:
                vals = pool[f"h{h}"].dropna()
                if len(vals) < 30:
                    row[f"h{h}_n"] = len(vals)
                    continue
                row[f"h{h}_n"] = len(vals)
                row[f"h{h}_mean"] = round(float(vals.mean()), 3)
                for q in QUANTILES:
                    row[f"h{h}_{QUANTILE_COLS[q]}"] = round(float(vals.quantile(q)), 3)
                if h == 24:
                    row["h24_p6freq"] = round(float((vals >= POWDER_ALERT_INCHES).mean()), 4)
            rows.append(row)
    climo = pd.DataFrame(rows)
    out = data_dir() / CLIMO_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    climo.to_csv(out, index=False)
    log.info("wrote %s (%d rows)", out, len(climo))
    return climo


def load_climatology() -> pd.DataFrame:
    return pd.read_csv(data_dir() / CLIMO_PATH)


def climatology_prediction(target_day: date, climo: pd.DataFrame | None = None) -> pd.DataFrame:
    """Climatology baseline submission for a round: median point forecast,
    full quantiles, and empirical powder-day probability."""
    climo = load_climatology() if climo is None else climo
    doy = min(target_day.timetuple().tm_yday, 365)
    day = climo[climo["doy"] == doy]
    rows = []
    for _, r in day.iterrows():
        for h in HORIZONS:
            if pd.isna(r.get(f"h{h}_mean")):
                continue
            row = {
                "station_id": r["station_id"],
                "horizon_h": h,
                "snowfall_in": r[f"h{h}_p50"],
            }
            for q, col in QUANTILE_COLS.items():
                row[col] = r[f"h{h}_{col}"]
            if h == 24:
                row["prob_6in"] = r["h24_p6freq"]
            rows.append(row)
    return pd.DataFrame(rows)

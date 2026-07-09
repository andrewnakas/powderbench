"""Built-in baseline forecasters. They compete on the leaderboard like anyone
else — beating baseline-openmeteo is the whole game.

Each returns a submission DataFrame (validate.py schema) for one round of a
given league.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from . import HORIZONS
from .leagues import League
from .scoring import QUANTILE_COLS
from .stations import load_stations, station_ids
from . import climatology, openmeteo

log = logging.getLogger(__name__)

CLIMO_TEAM = "baseline-climatology"
PERSISTENCE_LOOKBACK_DAYS = 10  # ERA5 truth lags ~5 days; SNOTEL 1-2


def zeros_prediction(league: League) -> pd.DataFrame:
    rows = []
    for sid in station_ids(league.name):
        for h in HORIZONS:
            row = {"station_id": sid, "horizon_h": h, "snowfall_in": 0.0}
            row.update({col: 0.0 for col in QUANTILE_COLS.values()})
            if h == 24:
                row["prob_6in"] = 0.0
            rows.append(row)
    return pd.DataFrame(rows)


def persistence_prediction(target_day: date, obs_daily: pd.DataFrame) -> pd.DataFrame:
    """Tomorrow = the last observed day: h24 = last valid snow24, h48 = 2x, etc.
    obs_daily is a QC'd daily-truth frame covering the days before target_day."""
    rows = []
    lookback = [target_day - timedelta(days=i) for i in range(1, PERSISTENCE_LOOKBACK_DAYS + 1)]
    for sid, grp in obs_daily.groupby("station_id"):
        grp = grp.set_index("date")
        last = next(
            (float(grp.loc[d, "snow24"]) for d in lookback if d in grp.index and grp.loc[d, "valid"]),
            None,
        )
        if last is None:
            continue
        for h in HORIZONS:
            rows.append(
                {"station_id": sid, "horizon_h": h, "snowfall_in": last * (h // 24)}
            )
    return pd.DataFrame(rows)


def openmeteo_prediction(league: League, target_day: date, model: str = "best_match", mode: str = "live") -> pd.DataFrame:
    """NWP baseline: cumulative daily snowfall over the round's 3 target days."""
    stations = list(load_stations(league.name))
    end = target_day + timedelta(days=2)
    fetch = openmeteo.forecast_daily_snowfall if mode == "live" else openmeteo.hindcast_daily_snowfall
    daily = fetch(stations, target_day, end, model=model)
    rows = []
    for sid, grp in daily.groupby("station_id"):
        grp = grp.set_index("date")["snowfall_in"]
        days = [target_day + timedelta(days=i) for i in range(3)]
        vals = [grp.get(d) for d in days]
        if any(v is None or pd.isna(v) for v in vals):
            continue
        cum = 0.0
        for h, v in zip(HORIZONS, vals):
            cum += float(v)
            rows.append({"station_id": sid, "horizon_h": h, "snowfall_in": round(cum, 3)})
    return pd.DataFrame(rows)


def all_baselines(league: League, target_day: date, mode: str = "live") -> dict[str, pd.DataFrame]:
    """All baseline submissions for a round, keyed by team name."""
    from .truth_sources import daily_truth

    obs_daily = daily_truth(
        league,
        station_ids(league.name),
        target_day - timedelta(days=PERSISTENCE_LOOKBACK_DAYS),
        target_day - timedelta(days=1),
    )
    subs = {
        "baseline-zeros": zeros_prediction(league),
        CLIMO_TEAM: climatology.climatology_prediction(target_day, league),
        "baseline-persistence": persistence_prediction(target_day, obs_daily),
        "baseline-openmeteo": openmeteo_prediction(league, target_day, "best_match", mode),
        "baseline-gfs": openmeteo_prediction(league, target_day, "gfs_seamless", mode),
    }
    return {k: v for k, v in subs.items() if len(v)}

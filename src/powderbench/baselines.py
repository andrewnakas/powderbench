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


def openmeteo_prediction(league: League, target_day: date, model: str = "best_match", mode: str = "live",
                         featured_only: bool = False) -> pd.DataFrame:
    """NWP baseline: cumulative daily snowfall over the round's 3 target days.
    featured_only keeps secondary models inside Open-Meteo's free daily call
    budget (it bills per location) once leagues run thousands of stations."""
    stations = list(load_stations(league.name, featured_only=featured_only))
    end = target_day + timedelta(days=2)
    fetch = openmeteo.forecast_daily_snowfall if mode == "live" else openmeteo.hindcast_daily_snowfall
    daily = fetch(stations, target_day, end, model=model)
    rows = []
    for sid, grp in daily.groupby("station_id"):
        grp = grp.set_index("date")["snowfall_in"]
        days = [target_day + timedelta(days=i) for i in range(3)]
        cum = 0.0
        # cumulative horizons up to the model's range (HRRR stops at 48h)
        for h, d in zip(HORIZONS, days):
            v = grp.get(d)
            if v is None or pd.isna(v):
                break
            cum += float(v)
            rows.append({"station_id": sid, "horizon_h": h, "snowfall_in": round(cum, 3)})
    return pd.DataFrame(rows)


# Extra NWP baselines, run on featured stations only (Open-Meteo's free tier
# bills per location). team -> (Open-Meteo model, region): "conus" models
# only cover the lower 48, "alps" only the Alps leagues.
MODEL_BASELINES = {
    "baseline-ecmwf": ("ecmwf_ifs025", None),
    "baseline-aifs": ("ecmwf_aifs025_single", None),  # ECMWF's AI model
    "baseline-icon": ("icon_seamless", None),
    "baseline-gem": ("gem_seamless", None),
    "baseline-ukmo": ("ukmo_seamless", None),
    "baseline-nbm": ("ncep_nbm_conus", "conus"),  # NOAA's calibrated National Blend
    "baseline-hrrr": ("ncep_hrrr_conus", "conus"),  # 3 km, 48h range
    "baseline-arome": ("meteofrance_seamless", "alps"),
}
CONSENSUS_TEAM = "baseline-consensus"


def _region_ok(league: League, region: str | None) -> bool:
    if region is None:
        return True
    sts = load_stations(league.name, featured_only=True)
    if region == "conus":
        return any(24 <= s.latitude <= 50 and -125 <= s.longitude <= -66 for s in sts)
    if region == "alps":
        return any(43 <= s.latitude <= 48.5 and 5 <= s.longitude <= 17 for s in sts)
    return False


def model_baselines(league: League, target_day: date, mode: str = "live") -> dict[str, pd.DataFrame]:
    out = {}
    for team, (model, region) in MODEL_BASELINES.items():
        if not _region_ok(league, region):
            continue
        try:
            pred = openmeteo_prediction(league, target_day, model, mode, featured_only=True)
        except Exception:
            log.warning("%s failed for %s/%s", team, league.name, target_day, exc_info=True)
            continue
        if len(pred):
            out[team] = pred
    return out


def consensus_prediction(subs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Median of the NWP baselines per station-horizon (needs >= 3 models) —
    the WxChallenge-style 'beat the crowd' target."""
    frames = [p[["station_id", "horizon_h", "snowfall_in"]] for t, p in subs.items()
              if t not in ("baseline-zeros", CLIMO_TEAM, "baseline-persistence", CONSENSUS_TEAM) and len(p)]
    if not frames:
        return pd.DataFrame()
    allp = pd.concat(frames)
    g = allp.groupby(["station_id", "horizon_h"])["snowfall_in"]
    med = g.median().round(3)[g.count() >= 3]
    return med.reset_index()


def _in_conus(league: League) -> bool:
    return any(24 <= s.latitude <= 50 and -125 <= s.longitude <= -66 for s in load_stations(league.name))


def all_baselines(league: League, target_day: date, mode: str = "live") -> dict[str, pd.DataFrame]:
    """All baseline submissions for a round, keyed by team name."""
    from .truth_sources import daily_truth

    def safe(team, fn):
        # one model/API outage must never cost the round its other baselines
        try:
            return fn()
        except Exception:
            log.warning("%s failed for %s/%s", team, league.name, target_day, exc_info=True)
            return pd.DataFrame()

    def persistence():
        obs_daily = daily_truth(
            league,
            station_ids(league.name),
            target_day - timedelta(days=PERSISTENCE_LOOKBACK_DAYS),
            target_day - timedelta(days=1),
        )
        return persistence_prediction(target_day, obs_daily)

    subs = {
        "baseline-zeros": zeros_prediction(league),
        CLIMO_TEAM: safe(CLIMO_TEAM, lambda: climatology.climatology_prediction(target_day, league)),
        "baseline-persistence": safe("baseline-persistence", persistence),
        "baseline-openmeteo": safe("baseline-openmeteo",
                                   lambda: openmeteo_prediction(league, target_day, "best_match", mode)),
        "baseline-gfs": safe("baseline-gfs", lambda: openmeteo_prediction(
            league, target_day, "gfs_seamless", mode, featured_only=True)),
    }
    subs.update(model_baselines(league, target_day, mode))
    if mode == "live" and _in_conus(league):
        from .wpc import wpc_prediction

        try:
            subs["baseline-wpc"] = wpc_prediction(list(load_stations(league.name)), target_day)
        except Exception:
            log.warning("WPC baseline failed for %s/%s", league.name, target_day, exc_info=True)
    subs[CONSENSUS_TEAM] = consensus_prediction(subs)
    return {k: v for k, v in subs.items() if len(v)}

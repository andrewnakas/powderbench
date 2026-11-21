"""Built-in baseline forecasters. They compete on the leaderboard like anyone
else — beating baseline-openmeteo is the whole game.

Each returns a submission DataFrame (validate.py schema) for one round of a
given league.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import HORIZONS
from .leagues import League
from .scoring import QUANTILE_COLS
from .stations import load_stations, station_ids
from . import climatology, nwp, openmeteo

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


def _cumulative(daily: pd.DataFrame, target_day: date) -> pd.DataFrame:
    """Daily snowfall (station_id, date, snowfall_in) -> cumulative horizons."""
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


def openmeteo_prediction(league: League, target_day: date, model: str = "best_match", mode: str = "live",
                         featured_only: bool = False) -> pd.DataFrame:
    """NWP baseline via Open-Meteo: cumulative daily snowfall over the round's
    3 target days. featured_only keeps secondary models inside Open-Meteo's
    free daily call budget (it bills per location)."""
    stations = list(load_stations(league.name, featured_only=featured_only))
    end = target_day + timedelta(days=2)
    fetch = openmeteo.forecast_daily_snowfall if mode == "live" else openmeteo.hindcast_daily_snowfall
    return _cumulative(fetch(stations, target_day, end, model=model), target_day)


# Baselines are generated 2.5-9 h before the cutoff; a hindcast takes the run
# that was online 3 h before it, like the NDFD baseline.
HINDCAST_SLOT_H = 3


def nwp_as_of(league: League, target_day: date, mode: str) -> datetime:
    """The moment whose newest model run a baseline may use: now for a live
    round (never past its cutoff), the baselines slot for a hindcast."""
    cutoff = league.cutoff_utc(target_day)
    if mode == "live":
        return min(datetime.now(timezone.utc), cutoff)
    return cutoff - timedelta(hours=HINDCAST_SLOT_H)


def direct_prediction(league: League, target_day: date, source: str, mode: str = "live",
                      featured_only: bool = False, stations=None) -> pd.DataFrame:
    """NWP baseline read from the producer's own files (nwp/), no Open-Meteo
    calls; `stations` defaults to the league's (featured) stations."""
    if stations is None:
        stations = list(load_stations(league.name, featured_only=featured_only))
    daily = nwp.daily_snowfall(stations, target_day, target_day + timedelta(days=2), source,
                               as_of=nwp_as_of(league, target_day, mode), live=mode == "live")
    log.info("%s %s: %s run %s for %d stations (direct)", league.name, target_day, source,
             daily.attrs.get("run"), len(stations))
    return _cumulative(daily, target_day)


# Extra NWP baselines. team -> (direct source, Open-Meteo model, region).
# A team with a direct source (nwp.SOURCES) reads the producer's own files at
# every station; see DIRECT_FIRST for which goes first. Teams without one use
# Open-Meteo on featured stations only (its free tier bills per location).
# Regions: "conus" covers the lower 48, "alps" the Alps leagues, "japan" Japan.
MODEL_BASELINES = {
    "baseline-ecmwf": ("ecmwf_ifs", "ecmwf_ifs025", None),
    "baseline-aifs": ("ecmwf_aifs", "ecmwf_aifs025_single", None),  # ECMWF's AI model
    "baseline-gfs": ("gfs", "gfs_seamless", None),
    "baseline-icon": ("om_icon", "icon_seamless", None),  # direct = Open-Meteo's open-data archive
    "baseline-gem": ("om_gem", "gem_seamless", None),
    "baseline-ukmo": ("om_ukmo", "ukmo_seamless", None),
    "baseline-nbm": ("nbm", "ncep_nbm_conus", "conus"),  # NOAA's calibrated National Blend
    "baseline-hrrr": (None, "ncep_hrrr_conus", "conus"),  # 3 km, 48h range
    "baseline-arome": (None, "meteofrance_seamless", "alps"),
    "baseline-jma": (None, "jma_seamless", "japan"),  # JMA MSM 5 km + GSM
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
    if region == "japan":
        return any(s.state == "JP" for s in sts)
    return False


def direct_enabled() -> bool:
    """PB_NWP_DIRECT=0 sends every model baseline back through Open-Meteo."""
    return os.environ.get("PB_NWP_DIRECT", "1") != "0"


# The direct read goes first for these: with Open-Meteo's own elevation rule
# (nwp/sources.py) it scores better than Open-Meteo's processing of the same
# model on featured stations (2026-11-01..14, DATA.md), for GFS even against
# Open-Meteo's HRRR blend. NBM's own snowfall (ASNOW) beats Open-Meteo's NBM
# by a wide margin. Open-Meteo then only fills featured stations the direct
# read missed.
DIRECT_FIRST = {"baseline-ecmwf", "baseline-aifs", "baseline-gfs", "baseline-nbm"}


def _direct_first(league: League, target_day: date, team: str, source: str, om_model: str, mode: str,
                  featured_only: bool) -> pd.DataFrame:
    direct = pd.DataFrame()
    try:
        direct = direct_prediction(league, target_day, source, mode,
                                   stations=list(load_stations(league.name, featured_only=featured_only)))
    except Exception:  # noqa: BLE001 - Open-Meteo still covers the featured stations
        log.warning("%s: direct %s failed", team, source, exc_info=True)
    covered = set(direct["station_id"]) if len(direct) else set()
    if all(s.station_id in covered for s in load_stations(league.name, featured_only=True)):
        return direct
    try:
        om = openmeteo_prediction(league, target_day, om_model, mode, featured_only=True)
    except Exception as exc:  # noqa: BLE001
        if not len(direct):
            raise
        if not isinstance(exc, openmeteo.OpenMeteoDisabled):
            log.warning("%s: Open-Meteo %s failed", team, om_model, exc_info=True)
        return direct
    om = om[~om["station_id"].isin(covered)] if len(om) else om
    return pd.concat([direct, om], ignore_index=True) if len(direct) else om


def model_prediction(league: League, target_day: date, team: str, mode: str = "live",
                     featured_only: bool = False) -> pd.DataFrame:
    """Open-Meteo on featured stations, then the direct read for every station
    it did not cover (all of them when Open-Meteo is unavailable). DIRECT_FIRST
    teams the other way round."""
    source, om_model, _ = MODEL_BASELINES[team]
    if source and direct_enabled() and team in DIRECT_FIRST:
        return _direct_first(league, target_day, team, source, om_model, mode, featured_only)
    om, om_error = pd.DataFrame(), None
    try:
        om = openmeteo_prediction(league, target_day, om_model, mode, featured_only=True)
    except Exception as exc:  # noqa: BLE001 - one source's outage must not cost the other
        om_error = exc
        if not isinstance(exc, openmeteo.OpenMeteoDisabled):
            log.warning("%s: Open-Meteo %s failed", team, om_model, exc_info=True)
    if not source or not direct_enabled():
        if om_error is not None:
            raise om_error
        return om
    covered = set(om["station_id"]) if len(om) else set()
    rest = [s for s in load_stations(league.name, featured_only=featured_only) if s.station_id not in covered]
    if not rest:
        return om
    try:
        direct = direct_prediction(league, target_day, source, mode, stations=rest)
    except Exception:
        log.warning("%s: direct %s failed for %d stations", team, source, len(rest), exc_info=True)
        if om_error is not None:
            raise om_error
        return om
    return pd.concat([om, direct], ignore_index=True) if len(om) else direct


def model_baselines(league: League, target_day: date, mode: str = "live",
                    featured_only: bool = False) -> dict[str, pd.DataFrame]:
    """featured_only limits the direct models too (hindcasts that score only
    the headline stations); live rounds give them every station."""
    out = {}
    for team, (_, _, region) in MODEL_BASELINES.items():
        if not _region_ok(league, region):
            continue
        try:
            pred = model_prediction(league, target_day, team, mode, featured_only)
        except openmeteo.OpenMeteoDisabled:
            log.info("%s skipped: Open-Meteo is off", team)
            continue
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
    }
    subs.update(model_baselines(league, target_day, mode))
    if mode == "live" and _in_conus(league):
        from .wpc import wpc_prediction

        try:
            subs["baseline-wpc"] = wpc_prediction(list(load_stations(league.name)), target_day)
        except Exception:
            log.warning("WPC baseline failed for %s/%s", league.name, target_day, exc_info=True)
        from .ndfd import ndfd_prediction

        # the newest NWS issuance at the baselines slot, never a later one
        slot = league.cutoff_utc(target_day) - timedelta(hours=3)
        subs["baseline-ndfd"] = safe("baseline-ndfd", lambda: ndfd_prediction(
            list(load_stations(league.name)), target_day, min(slot, datetime.now(timezone.utc))))
    subs[CONSENSUS_TEAM] = consensus_prediction(subs)
    return {k: v for k, v in subs.items() if len(v)}

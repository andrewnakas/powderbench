"""Training camp: score forecasts against past seasons, offline.

Hindcast rounds use the same truth pipeline and scoring as live rounds. The
NWP baselines come from Open-Meteo's archive of *actual past model runs*
(short lead time), so treat their 48h/72h skill as optimistic. Hindcast
results are for practice and never count toward the official leaderboard.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import baselines, climatology, scoring, snotel, truth
from .stations import station_ids

log = logging.getLogger(__name__)


def load_hindcast_submission(path: Path | str) -> dict[date, pd.DataFrame]:
    """A hindcast submission CSV is the normal schema plus a round_date column."""
    df = pd.read_csv(path)
    if "round_date" not in df.columns:
        raise ValueError("hindcast submissions need a round_date column (YYYY-MM-DD)")
    df["round_date"] = pd.to_datetime(df["round_date"]).dt.date
    return {d: g.drop(columns=["round_date"]) for d, g in df.groupby("round_date")}


def run_hindcast(
    begin: date,
    end: date,
    submissions: dict[str, dict[date, pd.DataFrame]] | None = None,
    include_baselines: bool = True,
) -> pd.DataFrame:
    """Score every round day in [begin, end]. Returns per-(team, round) metric rows."""
    submissions = submissions or {}
    ids = station_ids()
    obs = snotel.fetch_daily(ids, begin - timedelta(days=6), end + timedelta(days=3))
    daily = truth.daily_snowfall(obs)
    climo = climatology.load_climatology()

    rows = []
    day = begin
    while day <= end:
        truth_day = truth.window_truth(daily, day)
        climo_pred = climatology.climatology_prediction(day, climo)
        subs: dict[str, pd.DataFrame] = {}
        if include_baselines:
            subs.update(
                {
                    "baseline-zeros": baselines.zeros_prediction(),
                    baselines.CLIMO_TEAM: climo_pred,
                    "baseline-persistence": baselines.persistence_prediction(day, daily),
                    "baseline-openmeteo": baselines.openmeteo_prediction(day, "best_match", "hindcast"),
                    "baseline-gfs": baselines.openmeteo_prediction(day, "gfs_seamless", "hindcast"),
                }
            )
        for team, by_round in submissions.items():
            if day in by_round:
                subs[team] = by_round[day]
        for team, pred in subs.items():
            if not len(pred):
                continue
            metrics = scoring.score_round(pred, truth_day, climo_pred=climo_pred)
            metrics.pop("mae_by_horizon", None)
            rows.append({"team": team, "round": day.isoformat(), **metrics})
        log.info("scored %s (%d teams)", day, len(subs))
        day += timedelta(days=1)
    return pd.DataFrame(rows)

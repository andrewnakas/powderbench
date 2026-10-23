"""Replay past rounds: run an agent with only what it could have known 3h
before each round's cutoff, score it against that round's truth, and put it
next to the stored baselines."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .. import climatology, scoring
from ..leagues import League
from ..rounds import round_dir, submissions_dir

LEAD = timedelta(hours=3)  # agents run with the baselines, 3h before the cutoff


def backtest(league: League, agent, begin: date, end: date, compare=("baseline-consensus", "baseline-openmeteo",
                                                                   "baseline-gfs", "baseline-climatology")) -> pd.DataFrame:
    rows = []
    d = begin
    while d <= end:
        tpath = round_dir(league, d) / "truth.csv"
        if tpath.exists():
            truth = pd.read_csv(tpath)
            truth["valid"] = truth["valid"].astype(str) == "True"
            climo = climatology.climatology_prediction(d, league)
            pred = agent(league, d, league.cutoff_utc(d) - LEAD)
            teams = {"agent": pred}
            for t in compare:
                p = submissions_dir(league, d) / f"{t}.csv"
                if p.exists():
                    teams[t] = pd.read_csv(p)
            # score everyone on the same station-horizons the agent covered
            keys = pred[["station_id", "horizon_h"]] if len(pred) else truth[["station_id", "horizon_h"]]
            t_sub = truth.merge(keys, on=["station_id", "horizon_h"])
            for team, p in teams.items():
                if len(p):
                    m = scoring.score_round(p, t_sub, climo_pred=climo)
                    rows.append({"round": d.isoformat(), "team": team, "powder_score": m["powder_score"],
                                 "mae": m["mae"], "pinball": m.get("pinball"), "n": m["n_scored"]})
        d += timedelta(days=1)
    return pd.DataFrame(rows)

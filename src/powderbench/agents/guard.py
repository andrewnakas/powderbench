"""Safety rails for the learned agents (analog, gbm), which can only forecast
amounts they have seen: early in a season, or on a storm bigger than anything
in the history, they would cap the forecast.

- `enough_history`: abstain until there are MIN_ROUNDS resolved rounds and
  every horizon has MIN_SNOW_CASES cases with at least SNOW_CASE_IN of truth.
- `fallback_out_of_range`: rows whose model consensus is beyond the history's
  RANGE_Q quantile take house-blend's (multiplicative, extrapolating) values.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from .. import HORIZONS
from ..leagues import League

MIN_ROUNDS = 20
MIN_SNOW_CASES = 40
SNOW_CASE_IN = 2.0
RANGE_Q = 0.99


def enough_history(hist: pd.DataFrame) -> bool:
    if not len(hist) or hist["round"].nunique() < MIN_ROUNDS:
        return False
    cases = hist.drop_duplicates(["station_id", "horizon_h", "round"])
    snowy = cases[cases["truth_in"] >= SNOW_CASE_IN].groupby("horizon_h").size()
    return all(snowy.get(h, 0) >= MIN_SNOW_CASES for h in HORIZONS)


def fallback_out_of_range(out: pd.DataFrame, today: pd.DataFrame, hist: pd.DataFrame,
                          league: League, d: date, as_of: datetime) -> pd.DataFrame:
    if not len(out):
        return out
    keys = ["station_id", "horizon_h"]
    cons_hist = hist.groupby(keys + ["round"])["pred"].median().reset_index()
    cap = cons_hist.groupby("horizon_h")["pred"].quantile(RANGE_Q)
    cons = today.groupby(keys)["pred"].median().rename("cons").reset_index()
    cons["cap"] = cons["horizon_h"].map(cap)
    far = cons[cons["cons"] > cons["cap"]][keys]
    if not len(far):
        return out
    from .blend import blend_agent

    b = blend_agent(league, d, as_of).merge(far, on=keys)
    out = out.set_index(keys)
    b = b.set_index(keys)[[c for c in out.columns if c in b.columns]]
    out.update(b)
    return out.reset_index()

"""house-gbm: gradient-boosted quantile post-processing, retrained every run.

Each run it fits LightGBM quantile models (p10..p90, the median doubling as
the point forecast) on the league's resolved history up to `as_of`: features
are every model's forecast (missing where a model didn't run), the model
consensus and spread, the station's climatology, location, elevation and time
of year, per horizon. No offline training step, so it improves as the season's
history grows — and it can't see the future (see backtest).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .. import HORIZONS, POWDER_ALERT_INCHES, climatology
from ..leagues import League
from ..stations import load_stations
from .analog import _climo_p50
from .blend import _model_preds, history

MIN_ROWS = 400
QUANTILES = {"p10": 0.1, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p90": 0.9}
PARAMS = dict(n_estimators=250, learning_rate=0.04, num_leaves=15, min_child_samples=20,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1)


def _wide(long: pd.DataFrame, models: list[str], keys: list[str]) -> pd.DataFrame:
    w = long.pivot_table(index=keys, columns="model", values="pred", aggfunc="first")
    w = w.reindex(columns=models)
    w.columns = [f"m_{c.removeprefix('baseline-')}" for c in w.columns]
    stats = long.groupby(keys)["pred"].agg(cons="median", spread="std", nmod="size")
    out = w.join(stats)
    if "truth_in" in long:
        out = out.join(long.groupby(keys)["truth_in"].first().rename("truth"))
    return out.reset_index()


def _add_static(df: pd.DataFrame, league: League, climo: pd.DataFrame, doys) -> pd.DataFrame:
    meta = {s.station_id: s for s in load_stations(league.name)}
    df["doy"] = doys
    df["doy_sin"] = np.sin(2 * np.pi * df["doy"] / 365)
    df["doy_cos"] = np.cos(2 * np.pi * df["doy"] / 365)
    df["climo"] = _climo_p50(climo, df["station_id"], df["horizon_h"], df["doy"])
    df["lat"] = [meta[s].latitude if s in meta else np.nan for s in df["station_id"]]
    df["lon"] = [meta[s].longitude if s in meta else np.nan for s in df["station_id"]]
    df["elev"] = [meta[s].elevation_ft if s in meta else np.nan for s in df["station_id"]]
    return df


def gbm_agent(league: League, d: date, as_of: datetime | None = None) -> pd.DataFrame:
    try:
        import lightgbm as lgb
    except ImportError:  # optional dependency: pip install -e .[agents]
        return pd.DataFrame()
    as_of = as_of or datetime.now(timezone.utc)
    today = _model_preds(league, d)
    hist = history(league, as_of)
    if not len(today) or len(hist) < MIN_ROWS:
        return pd.DataFrame()
    try:
        climo = climatology.load_climatology(league)
    except FileNotFoundError:
        climo = pd.DataFrame(columns=["station_id", "doy"])
    models = sorted(set(hist["model"]) | set(today["model"]))

    train = _wide(hist, models, ["station_id", "horizon_h", "round"])
    train = _add_static(train, league, climo, pd.to_datetime(train["round"]).dt.dayofyear.to_numpy())
    test = _wide(today, models, ["station_id", "horizon_h"])
    test = _add_static(test, league, climo, np.full(len(test), d.timetuple().tm_yday))
    feats = [c for c in train.columns if c.startswith("m_")] + \
        ["cons", "spread", "nmod", "climo", "doy_sin", "doy_cos", "lat", "lon", "elev", "horizon_h"]
    if len(train) < MIN_ROWS:
        return pd.DataFrame()

    out = test[["station_id", "horizon_h"]].copy()
    for name, q in QUANTILES.items():
        m = lgb.LGBMRegressor(objective="quantile", alpha=q, **PARAMS)
        m.fit(train[feats], train["truth"])
        out[name] = np.clip(m.predict(test[feats]), 0, None)
    q = np.sort(out[list(QUANTILES)].to_numpy(), axis=1)  # monotone quantiles
    out[list(QUANTILES)] = np.round(q, 2)
    out["snowfall_in"] = out["p50"]
    # P(>= 6") on the 24h rows: interpolate the predicted quantile curve
    qs = np.array(list(QUANTILES.values()))
    out["prob_6in"] = np.nan
    h24 = out["horizon_h"] == 24
    out.loc[h24, "prob_6in"] = [round(float(1 - np.interp(POWDER_ALERT_INCHES, row, qs, left=0.0, right=1.0)), 3)
                                if row[-1] > row[0] else float(row[0] >= POWDER_ALERT_INCHES)
                                for row in q[h24.to_numpy()]]
    return out[["station_id", "horizon_h", "snowfall_in", *QUANTILES, "prob_6in"]]

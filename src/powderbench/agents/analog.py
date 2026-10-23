"""house-analog: "what actually fell the last times the models looked like this?"

For each station-horizon it finds the K most similar past cases in the
league's resolved history — similar model consensus and spread, similar
climatology and time of year, same horizon, with a preference for the same
station — and forecasts the (weighted) distribution of their observed truth:
median as the point, its quantiles as p10..p90, the share >= 6" as prob_6in.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .. import HORIZONS, POWDER_ALERT_INCHES, climatology
from ..leagues import League
from .blend import _model_preds, history

K = 40
OTHER_STATION_PENALTY = 0.35  # extra distance for an analog from another station
MIN_CASES = 60
QUANTILES = {"p10": 0.1, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p90": 0.9}


def _features(rows: pd.DataFrame, climo: pd.DataFrame) -> pd.DataFrame:
    """rows: station_id, horizon_h, model, pred[, truth_in, round] (long) ->
    one row per station-horizon(-round) with analog features."""
    keys = ["station_id", "horizon_h"] + (["round"] if "round" in rows else [])
    agg = rows.groupby(keys).agg(cons=("pred", "median"), spread=("pred", "std"), nmod=("pred", "size"))
    if "truth_in" in rows:
        agg["truth"] = rows.groupby(keys)["truth_in"].first()
    agg = agg.reset_index()
    agg["spread"] = agg["spread"].fillna(0.0)
    day = pd.to_datetime(agg["round"]) if "round" in agg else None
    return agg, day


def _climo_p50(climo: pd.DataFrame, sids, horizons, doys) -> np.ndarray:
    idx = climo.set_index(["station_id", "doy"])
    out = []
    for s, h, d in zip(sids, horizons, doys):
        key = (s, min(int(d), 365))
        col = f"h{int(h)}_p50"
        out.append(idx.at[key, col] if key in idx.index and col in idx.columns and pd.notna(idx.at[key, col]) else 0.0)
    return np.asarray(out, dtype=float)


def _matrix(df: pd.DataFrame) -> np.ndarray:
    ang = 2 * np.pi * df["doy"].to_numpy() / 365.0
    return np.column_stack([
        np.log1p(df["cons"].clip(lower=0)),
        0.7 * np.log1p(df["spread"].clip(lower=0)),
        0.5 * np.log1p(df["climo"].clip(lower=0)),
        0.3 * np.sin(ang), 0.3 * np.cos(ang),
    ])


def _wquantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    c = np.cumsum(w) / w.sum()
    return float(v[np.searchsorted(c, q, side="left").clip(0, len(v) - 1)])


def analog_agent(league: League, d: date, as_of: datetime | None = None) -> pd.DataFrame:
    as_of = as_of or datetime.now(timezone.utc)
    today = _model_preds(league, d)
    hist = history(league, as_of)
    if not len(today) or len(hist) < MIN_CASES:
        return pd.DataFrame()
    try:
        climo = climatology.load_climatology(league)
    except FileNotFoundError:
        climo = pd.DataFrame(columns=["station_id", "doy"])

    past, pday = _features(hist, climo)
    past["doy"] = pday.dt.dayofyear.to_numpy()
    past["climo"] = _climo_p50(climo, past["station_id"], past["horizon_h"], past["doy"])
    now, _ = _features(today, climo)
    now["doy"] = d.timetuple().tm_yday
    now["climo"] = _climo_p50(climo, now["station_id"], now["horizon_h"], now["doy"])

    rows = []
    for h in HORIZONS:
        P = past[past["horizon_h"] == h].reset_index(drop=True)
        N = now[now["horizon_h"] == h].reset_index(drop=True)
        if len(P) < MIN_CASES or not len(N):
            continue
        XP, XN = _matrix(P), _matrix(N)
        truths, p_sid = P["truth"].to_numpy(dtype=float), P["station_id"].to_numpy()
        for i, r in N.iterrows():
            dist = np.sqrt(((XP - XN[i]) ** 2).sum(axis=1)) + OTHER_STATION_PENALTY * (p_sid != r["station_id"])
            k = np.argsort(dist)[: min(K, len(dist))]
            w = 1.0 / (dist[k] + 0.05)
            t = truths[k]
            row = {"station_id": r["station_id"], "horizon_h": h}
            for name, q in QUANTILES.items():
                row[name] = round(max(_wquantile(t, w, q), 0.0), 2)
            row["snowfall_in"] = row["p50"]
            if h == 24:
                row["prob_6in"] = round(float(np.sum(w * (t >= POWDER_ALERT_INCHES)) / w.sum()), 3)
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)[["station_id", "horizon_h", "snowfall_in", *QUANTILES, "prob_6in"]]

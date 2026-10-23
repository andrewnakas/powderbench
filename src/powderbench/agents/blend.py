"""house-blend: a skill-weighted, bias-corrected blend of the weather models.

For each station and horizon it learns, from rounds already resolved, (1) how
accurate each model has been there — weights ∝ 1/(MAE + 0.1)², the station's
MAE shrunk toward the league-wide MAE when the station has little history —
and (2) how biased the blend runs there — a truth/forecast ratio shrunk toward
1. Quantiles and P(>=6") come from the league's empirical residuals.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .. import HORIZONS, POWDER_ALERT_INCHES
from ..leagues import League
from ..rounds import round_dir, submissions_dir
from ..stations import data_dir

NON_MODELS = {"baseline-zeros", "baseline-climatology", "baseline-persistence", "baseline-consensus"}
SHRINK_K = 10.0  # pseudo-rounds pulling a station toward the league mean
LOOKBACK_ROUNDS = 60
QUANTILES = {"p10": 0.1, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p90": 0.9}


def _model_preds(league: League, d: date) -> pd.DataFrame:
    """station_id, horizon_h, model, pred — the NWP baselines of round d."""
    rows = []
    for p in sorted(submissions_dir(league, d).glob("baseline-*.csv")):
        if p.stem in NON_MODELS:
            continue
        df = pd.read_csv(p, usecols=["station_id", "horizon_h", "snowfall_in"])
        rows.append(df.rename(columns={"snowfall_in": "pred"}).assign(model=p.stem))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["station_id", "horizon_h", "pred", "model"])


def history(league: League, as_of: datetime) -> pd.DataFrame:
    """Model forecasts joined with truth for rounds resolved before `as_of`."""
    root = data_dir() / "rounds" / league.name
    if not root.exists():
        return pd.DataFrame()
    days = []
    for rdir in sorted(root.iterdir()):
        try:
            d = date.fromisoformat(rdir.name)
        except ValueError:
            continue
        if (rdir / "truth.csv").exists() and league.matured_at_utc(d) <= as_of:
            days.append(d)
    frames = []
    for d in days[-LOOKBACK_ROUNDS:]:
        t = pd.read_csv(round_dir(league, d) / "truth.csv")
        t = t[t["valid"].astype(str) == "True"][["station_id", "horizon_h", "truth_in"]]
        m = _model_preds(league, d)
        if len(m) and len(t):
            frames.append(m.merge(t, on=["station_id", "horizon_h"]).assign(round=d))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def blend_agent(league: League, d: date, as_of: datetime | None = None) -> pd.DataFrame:
    as_of = as_of or datetime.now(timezone.utc)
    today = _model_preds(league, d)
    if not len(today):
        return pd.DataFrame()
    hist = history(league, as_of)

    # model skill: station MAE shrunk toward the league MAE (per horizon)
    if len(hist):
        hist["err"] = (hist["pred"] - hist["truth_in"]).abs()
        lg = hist.groupby(["horizon_h", "model"])["err"].mean().rename("mae_lg")
        st = hist.groupby(["station_id", "horizon_h", "model"])["err"].agg(["mean", "count"])
        st.columns = ["mae_st", "n"]
        today = today.join(lg, on=["horizon_h", "model"]).join(st, on=["station_id", "horizon_h", "model"])
        today["n"] = today["n"].fillna(0)
        prior = today["mae_lg"].fillna(today["mae_lg"].mean() if today["mae_lg"].notna().any() else 1.0)
        mae = (today["n"] * today["mae_st"].fillna(0) + SHRINK_K * prior) / (today["n"] + SHRINK_K)
    else:
        mae = pd.Series(1.0, index=today.index)
    today["w"] = 1.0 / (mae + 0.1) ** 2
    blend = (today["pred"] * today["w"]).groupby([today["station_id"], today["horizon_h"]]).sum() / \
        today["w"].groupby([today["station_id"], today["horizon_h"]]).sum()
    out = blend.rename("blend").reset_index()

    # bias: truth / equal-weight model mean, per station-horizon, shrunk to 1
    if len(hist):
        hm = hist.groupby(["station_id", "horizon_h", "round"]).agg(pred=("pred", "mean"), truth=("truth_in", "first"))
        b = hm.groupby(["station_id", "horizon_h"]).agg(sp=("pred", "sum"), stru=("truth", "sum"), n=("pred", "size"))
        ratio = ((b["stru"] + SHRINK_K * 0.1) / (b["sp"] + SHRINK_K * 0.1)).clip(0.5, 2.0)
        w = b["n"] / (b["n"] + SHRINK_K)
        b["ratio"] = w * ratio + (1 - w)
        out = out.join(b["ratio"], on=["station_id", "horizon_h"])
        # residual distribution (league-wide per horizon) of truth vs. the corrected mean
        hm = hm.join(b["ratio"], on=["station_id", "horizon_h"])
        hm["resid"] = hm["truth"] - hm["pred"] * hm["ratio"]
        resid = {h: g["resid"].to_numpy() for h, g in hm.reset_index().groupby("horizon_h")}
    else:
        out["ratio"] = 1.0
        resid = {}
    out["ratio"] = out["ratio"].fillna(1.0)
    out["snowfall_in"] = (out["blend"] * out["ratio"]).clip(lower=0).round(2)

    for h in HORIZONS:
        sel = out["horizon_h"] == h
        r = resid.get(h)
        pts = out.loc[sel, "snowfall_in"].to_numpy()
        for name, q in QUANTILES.items():
            if r is not None and len(r) >= 30:
                out.loc[sel, name] = np.clip(pts + np.quantile(r, q), 0, None)
            else:  # no history yet: a simple multiplicative spread
                out.loc[sel, name] = np.clip(pts * {0.1: 0.2, 0.25: 0.5, 0.5: 1, 0.75: 1.5, 0.9: 2.2}[q], 0, None)
        if h == 24:
            if r is not None and len(r) >= 30:
                out.loc[sel, "prob_6in"] = [float(np.mean(p + r >= POWDER_ALERT_INCHES)) for p in pts]
            else:
                out.loc[sel, "prob_6in"] = (pts >= POWDER_ALERT_INCHES).astype(float) * 0.6 + 0.02
    q = out[list(QUANTILES)].to_numpy()
    out[list(QUANTILES)] = np.round(np.sort(q, axis=1), 2)
    out["prob_6in"] = out.get("prob_6in", pd.Series(dtype=float)).round(3)
    return out[["station_id", "horizon_h", "snowfall_in", *QUANTILES, "prob_6in"]]

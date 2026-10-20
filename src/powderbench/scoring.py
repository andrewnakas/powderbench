"""Scoring: MAE, Powder Score (skill vs. climatology), pinball loss, Brier.

All metrics are computed only on station-horizon rows whose truth passed QC.
The Powder Score compares a team's MAE to climatology's MAE *on the same rows
the team predicted*, so partial-coverage teams can't cherry-pick easy stations
for an edge.
"""

from __future__ import annotations

import pandas as pd

from . import POWDER_ALERT_INCHES, QUANTILES

QUANTILE_COLS = {q: f"p{int(q * 100)}" for q in QUANTILES}  # p10, p25, p50, p75, p90


def pinball_loss(q: float, pred: float, truth: float) -> float:
    diff = truth - pred
    return q * diff if diff >= 0 else (q - 1) * diff


def brier(prob: float, event: bool) -> float:
    return (prob - (1.0 if event else 0.0)) ** 2


def powder_score(mae_team: float, mae_ref: float) -> float | None:
    """100 x (1 - MAE_team / MAE_ref). 0 = climatology, 100 = perfect.
    None when the reference MAE is 0 (nothing to beat, e.g. a no-snow round)."""
    if mae_ref == 0:
        return None
    return 100.0 * (1.0 - mae_team / mae_ref)


def score_round(pred: pd.DataFrame, truth: pd.DataFrame, climo_pred: pd.DataFrame | None = None) -> dict:
    """Score one submission against one round's truth.

    pred: station_id, horizon_h, snowfall_in [, p10..p90, prob_6in]
    truth: station_id, horizon_h, truth_in, valid
    climo_pred: climatology submission (same schema as pred) for skill scores.

    Returns a metrics dict; NaN-free (missing metrics are None).
    """
    scorable = truth[truth["valid"]]
    merged = scorable.merge(pred, on=["station_id", "horizon_h"], how="left")
    merged = merged.dropna(subset=["snowfall_in"])
    n_scorable = len(scorable)
    n_scored = len(merged)
    metrics: dict = {
        "n_scorable": int(n_scorable),
        "n_scored": int(n_scored),
        "coverage": round(n_scored / n_scorable, 4) if n_scorable else None,
    }
    if n_scored == 0:
        metrics.update({"mae": None, "rmse": None, "bias": None, "powder_score": None,
                        "mae_by_horizon": {}, "pinball": None, "brier6": None})
        return metrics

    err = merged["snowfall_in"] - merged["truth_in"]
    metrics["mae"] = round(float(err.abs().mean()), 4)
    metrics["rmse"] = round(float((err**2).mean() ** 0.5), 4)
    metrics["bias"] = round(float(err.mean()), 4)
    metrics["mae_by_horizon"] = {
        int(h): round(float((g["snowfall_in"] - g["truth_in"]).abs().mean()), 4)
        for h, g in merged.groupby("horizon_h")
    }

    metrics["powder_score"] = None
    if climo_pred is not None:
        # skill is measured on the rows climatology also covers: a station
        # still building its climatology drops out of the comparison for
        # every team alike instead of voiding everyone's score
        ref = merged[["station_id", "horizon_h", "truth_in", "snowfall_in"]].merge(
            climo_pred[["station_id", "horizon_h", "snowfall_in"]].rename(columns={"snowfall_in": "climo_in"}),
            on=["station_id", "horizon_h"], how="inner",
        )
        if len(ref):
            mae_team = float((ref["snowfall_in"] - ref["truth_in"]).abs().mean())
            mae_ref = float((ref["climo_in"] - ref["truth_in"]).abs().mean())
            ps = powder_score(mae_team, mae_ref)
            metrics["powder_score"] = None if ps is None else round(ps, 2)
            metrics["n_skill"] = int(len(ref))

    metrics["pinball"] = None
    qcols = [c for c in QUANTILE_COLS.values() if c in merged.columns]
    if len(qcols) == len(QUANTILE_COLS):
        qrows = merged.dropna(subset=qcols)
        if len(qrows):
            losses = [
                pinball_loss(q, row[col], row["truth_in"])
                for _, row in qrows.iterrows()
                for q, col in QUANTILE_COLS.items()
            ]
            metrics["pinball"] = round(sum(losses) / len(losses), 4)
            metrics["n_pinball"] = int(len(qrows))

    metrics["brier6"] = None
    if "prob_6in" in merged.columns:
        h24 = merged[(merged["horizon_h"] == 24)].dropna(subset=["prob_6in"])
        if len(h24):
            scores = [
                brier(row["prob_6in"], row["truth_in"] >= POWDER_ALERT_INCHES)
                for _, row in h24.iterrows()
            ]
            metrics["brier6"] = round(sum(scores) / len(scores), 4)
            metrics["n_brier6"] = int(len(h24))
    return metrics


TRACK_FULL = "full"
REGION_PREFIX = "region:"
MIN_REGION_STATIONS = 3
_TRACK_KEYS = ("n_scorable", "n_scored", "coverage", "mae", "powder_score", "bias")


def score_tracks(pred: pd.DataFrame, truth: pd.DataFrame, climo_pred: pd.DataFrame | None,
                 stations: list) -> dict:
    """Headline metrics on the league's featured stations, plus slim side
    tracks: `full` (every station, when the league has non-featured ones) and
    one `region:<name>` track per region with enough stations.

    stations: Station objects in this round (the manifest snapshot)."""
    featured = {s.station_id for s in stations if s.featured}
    in_featured = truth["station_id"].isin(featured)
    metrics = score_round(pred[pred["station_id"].isin(featured)], truth[in_featured], climo_pred)

    tracks: dict[str, dict] = {}
    if len(featured) < len(stations):
        full = score_round(pred, truth, climo_pred)
        tracks[TRACK_FULL] = {k: full.get(k) for k in _TRACK_KEYS}
    regions: dict[str, set[str]] = {}
    for s in stations:
        regions.setdefault(s.region, set()).add(s.station_id)
    if len(regions) > 1:
        for region, ids in sorted(regions.items()):
            if len(ids) < MIN_REGION_STATIONS:
                continue
            sub = pred[pred["station_id"].isin(ids)]
            if not len(sub):
                continue
            r = score_round(sub, truth[truth["station_id"].isin(ids)], climo_pred)
            tracks[REGION_PREFIX + region] = {k: r.get(k) for k in _TRACK_KEYS}
    if tracks:
        metrics["tracks"] = tracks
    return metrics

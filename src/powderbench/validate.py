"""Submission validation: schema, ranges, coverage.

A submission is a CSV with one row per (station_id, horizon_h):
  required: station_id, horizon_h, snowfall_in
  optional: p10, p25, p50, p75, p90 (all five or none, non-decreasing)
  optional: prob_6in (only meaningful on horizon 24 rows)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import HORIZONS
from .scoring import QUANTILE_COLS
from .stations import station_ids

MAX_SNOWFALL_IN = 200.0
MIN_COVERAGE = 0.7  # fraction of station-horizons required for official ranking


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: float = 0.0

    def summary(self) -> str:
        lines = [f"{'PASS' if self.ok else 'FAIL'} — coverage {self.coverage:.0%}"]
        lines += [f"error: {e}" for e in self.errors]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def validate_submission(path: Path | str, league: object = None) -> ValidationResult:
    """league: League object or league name; default stations. The submission
    is checked against that league's station registry."""
    league_name = getattr(league, "name", league) or "stations"
    res = ValidationResult(ok=True)
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return ValidationResult(ok=False, errors=[f"could not parse CSV: {exc}"])

    required = {"station_id", "horizon_h", "snowfall_in"}
    missing = required - set(df.columns)
    if missing:
        return ValidationResult(ok=False, errors=[f"missing required columns: {sorted(missing)}"])

    known = set(station_ids(league_name))
    bad_stations = sorted(set(df["station_id"]) - known)
    if bad_stations:
        res.errors.append(f"unknown station_id(s): {bad_stations[:5]}")

    bad_h = sorted(set(df["horizon_h"]) - set(HORIZONS))
    if bad_h:
        res.errors.append(f"horizon_h must be one of {HORIZONS}, got: {bad_h[:5]}")

    if df.duplicated(["station_id", "horizon_h"]).any():
        res.errors.append("duplicate (station_id, horizon_h) rows")

    vals = pd.to_numeric(df["snowfall_in"], errors="coerce")
    if vals.isna().any():
        res.errors.append("snowfall_in contains non-numeric or empty values")
    elif ((vals < 0) | (vals > MAX_SNOWFALL_IN)).any():
        res.errors.append(f"snowfall_in must be within [0, {MAX_SNOWFALL_IN}]")

    qcols = list(QUANTILE_COLS.values())
    present_q = [c for c in qcols if c in df.columns]
    if present_q and len(present_q) != len(qcols):
        res.errors.append(f"quantile columns must be all-or-none of {qcols}, got {present_q}")
    elif present_q:
        qdf = df[qcols].apply(pd.to_numeric, errors="coerce")
        rows_with_q = qdf.notna().all(axis=1)
        if qdf.notna().any(axis=1).sum() != rows_with_q.sum():
            res.errors.append("rows with partial quantiles: fill all five or leave all blank")
        q = qdf[rows_with_q]
        if len(q) and not (q[qcols].diff(axis=1).iloc[:, 1:] >= -1e-9).all().all():
            res.errors.append("quantiles must be non-decreasing (p10 <= p25 <= ... <= p90)")
        if len(q) and ((q < 0) | (q > MAX_SNOWFALL_IN)).any().any():
            res.errors.append(f"quantiles must be within [0, {MAX_SNOWFALL_IN}]")

    if "prob_6in" in df.columns:
        p = pd.to_numeric(df["prob_6in"], errors="coerce").dropna()
        if ((p < 0) | (p > 1)).any():
            res.errors.append("prob_6in must be within [0, 1]")

    expected = len(known) * len(HORIZONS)
    valid_rows = df[df["station_id"].isin(known) & df["horizon_h"].isin(HORIZONS)]
    res.coverage = len(valid_rows.drop_duplicates(["station_id", "horizon_h"])) / expected
    if res.coverage < MIN_COVERAGE:
        res.warnings.append(
            f"coverage {res.coverage:.0%} is below {MIN_COVERAGE:.0%} — "
            "scored, but not eligible for official ranking this round"
        )

    res.ok = not res.errors
    return res

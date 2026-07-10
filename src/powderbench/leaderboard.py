"""Aggregate per-round scores into leaderboards.

Used by both the live pipeline (JSON round results on disk) and hindcast mode
(in-memory rows). Ranking rules:
  - official rank sorts by mean Powder Score across rounds where it exists
  - a team must appear in >= MIN_ROUNDS rounds and average >= MIN_COVERAGE
    coverage to be ranked; everyone is still listed
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from .leagues import League, get_league
from .stations import data_dir
from .validate import MIN_COVERAGE

MIN_ROUNDS = 5


def round_results_dir(league: League) -> Path:
    return data_dir() / "results" / league.name / "rounds"


def leaderboard_path(league: League) -> Path:
    return data_dir() / "results" / league.name / "leaderboard.json"


def aggregate(rows: pd.DataFrame, min_rounds: int = MIN_ROUNDS) -> pd.DataFrame:
    """rows: one row per (team, round) with metric columns from scoring.score_round
    (powder_score, mae, coverage, pinball, brier6, n_scored...).

    Returns one row per team, ranked (NaN rank = unranked/unofficial)."""
    teams = []
    for team, grp in rows.groupby("team"):
        entry = {
            "team": team,
            "is_baseline": team.startswith("baseline-"),
            "rounds": int(len(grp)),
            "avg_coverage": round(float(grp["coverage"].mean()), 4),
            "powder_score": _mean(grp["powder_score"]),
            "mae": _mean(grp["mae"]),
            "rmse": _mean(grp.get("rmse")),
            "bias": _mean(grp.get("bias")),
            "pinball": _mean(grp.get("pinball")),
            "brier6": _mean(grp.get("brier6")),
        }
        teams.append(entry)
    board = pd.DataFrame(teams)
    eligible = (
        (board["rounds"] >= min_rounds)
        & (board["avg_coverage"] >= MIN_COVERAGE)
        & board["powder_score"].notna()
    )
    board["eligible"] = eligible
    board = board.sort_values(
        by=["eligible", "powder_score"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True)
    board["rank"] = None
    board.loc[board["eligible"], "rank"] = range(1, int(board["eligible"].sum()) + 1)
    return board


def _records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records: pandas serializes NaN as null (browsers reject bare NaN)."""
    return json.loads(df.to_json(orient="records"))


def _mean(series) -> float | None:
    if series is None:
        return None
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(vals.mean()), 3) if len(vals) else None


def load_round_results(league: League | str = "stations") -> pd.DataFrame:
    """Flatten a league's resolved-round JSON files into (team, round) metric rows."""
    league = get_league(league) if isinstance(league, str) else league
    rows = []
    rounds_dir = round_results_dir(league)
    for path in sorted(rounds_dir.glob("*.json")):
        payload = json.loads(path.read_text())
        for team, metrics in payload["teams"].items():
            if metrics.get("invalid") or metrics.get("late"):
                continue  # scored for the team's own reference, never ranked
            row = {"team": team, "round": payload["round_id"], **metrics}
            row.pop("mae_by_horizon", None)
            rows.append(row)
    return pd.DataFrame(rows)


def build_leaderboard(league: League | str = "stations", season_start: date | None = None) -> dict:
    """Aggregate a league's resolved rounds into leaderboard.json."""
    league = get_league(league) if isinstance(league, str) else league
    rows = load_round_results(league)
    out = {"league": league.name, "status": league.status, "generated_rounds": 0, "season": [], "last30": []}
    if len(rows):
        if season_start is not None:
            rows = rows[rows["round"] >= season_start.isoformat()]
        round_ids = sorted(rows["round"].unique())
        out["generated_rounds"] = len(round_ids)
        out["season"] = _records(aggregate(rows))
        last30 = rows[rows["round"].isin(round_ids[-30:])]
        out["last30"] = _records(aggregate(last30, min_rounds=min(MIN_ROUNDS, len(round_ids[-30:]))))
        out["rounds"] = round_ids
    path = leaderboard_path(league)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str))
    return out

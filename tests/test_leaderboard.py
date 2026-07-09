import json

import pandas as pd

from powderbench import leaderboard


def rows_for(team, n_rounds, powder_score, coverage=1.0):
    return [
        {
            "team": team, "round": f"2025-03-{i+1:02d}", "powder_score": powder_score,
            "mae": 1.0, "rmse": 1.2, "bias": 0.1, "coverage": coverage,
            "pinball": None, "brier6": None,
        }
        for i in range(n_rounds)
    ]


def test_aggregate_ranks_eligible_teams_only():
    rows = pd.DataFrame(
        rows_for("sharp", 6, 30.0)
        + rows_for("baseline-zeros", 6, 10.0)
        + rows_for("newbie", 2, 50.0)  # too few rounds
        + rows_for("sniper", 6, 40.0, coverage=0.2)  # too little coverage
    )
    board = leaderboard.aggregate(rows)
    ranked = board[board["rank"].notna()].set_index("team")
    assert list(ranked.index) == ["sharp", "baseline-zeros"]
    assert ranked.loc["sharp", "rank"] == 1
    unranked = board[board["rank"].isna()]["team"].tolist()
    assert set(unranked) == {"newbie", "sniper"}
    assert bool(board.set_index("team").loc["baseline-zeros", "is_baseline"])


def test_load_round_results_skips_late_and_invalid(tmp_path, monkeypatch):
    from powderbench.leagues import League

    league = League(
        name="northern", label="n", status="live", truth_source="snotel",
        cutoff_days_before=0, cutoff_hour_utc=0, resolve_after_days=3,
        resolve_after_hour_utc=15, season_start_month=10,
    )
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    rounds = tmp_path / "results" / "northern" / "rounds"
    rounds.mkdir(parents=True)
    payload = {
        "round_id": "2025-03-01",
        "teams": {
            "good": {"mae": 1.0, "powder_score": 5.0, "coverage": 1.0, "late": False},
            "tardy": {"mae": 0.5, "powder_score": 50.0, "coverage": 1.0, "late": True},
            "broken": {"invalid": True, "errors": ["bad csv"]},
        },
    }
    (rounds / "2025-03-01.json").write_text(json.dumps(payload))
    rows = leaderboard.load_round_results(league)
    assert rows["team"].tolist() == ["good"]

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
        name="stations", label="n", status="live", truth_source="snotel",
        cutoff_days_before=0, cutoff_hour_utc=0, resolve_after_days=3,
        resolve_after_hour_utc=15, season_start_month=10,
    )
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    rounds = tmp_path / "results" / "stations" / "rounds"
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


def test_team_profiles_and_badges(tmp_path, monkeypatch):
    import json

    from powderbench import site
    from powderbench.leagues import get_league

    lg = get_league("era5")
    lb = tmp_path / "lb.json"
    lb.write_text(json.dumps({"season": [
        {"team": "frozen-gradient", "powder_score": 12.34, "eligible": True, "rank": 1, "rounds": 2},
        {"team": "baseline-gfs", "powder_score": -3.0, "eligible": True, "rank": 2, "rounds": 2}]}))
    monkeypatch.setattr(site, "leaderboard_path", lambda l: lb)
    monkeypatch.setattr(site, "site_dir", lambda: tmp_path)
    hist = [{"round_id": "2026-07-01", "teams": {"frozen-gradient": 20.0, "baseline-gfs": 5.0}},
            {"round_id": "2026-07-02", "teams": {"frozen-gradient": 4.0, "baseline-gfs": 9.0}}]
    teams = site._teams([lg], {"era5": hist})
    fg = teams["frozen-gradient"]
    assert fg["kind"] == "player" and teams["baseline-gfs"]["kind"] == "baseline"
    assert fg["leagues"]["era5"]["h2h"]["baseline-gfs"] == {"won": 1, "of": 2}
    assert fg["leagues"]["era5"]["rounds"] == [["2026-07-01", 20.0], ["2026-07-02", 4.0]]
    site.build_badges(teams)
    svg = (tmp_path / "badges" / "frozen-gradient.svg").read_text()
    assert svg.startswith("<svg") and "era5 +12.3 · #1" in svg


def test_monthly_champions():
    import pandas as pd

    from powderbench.leaderboard import monthly_champions

    rows = []
    for i in range(12):
        d = f"2026-08-{i + 1:02d}"
        for team, ps in (("frozen-gradient", 30.0), ("house-gbm", 25.0), ("baseline-gfs", 10.0),
                         ("baseline-climatology", 0.0), ("late-joiner", 90.0 if i > 9 else None)):
            if ps is not None:
                rows.append({"team": team, "round": d, "powder_score": ps, "mae": 1.0, "coverage": 1.0})
    rows.append({"team": "frozen-gradient", "round": "2026-09-01", "powder_score": 5.0, "mae": 1.0, "coverage": 1.0})
    m = monthly_champions(pd.DataFrame(rows))
    assert [x["month"] for x in m] == ["2026-09", "2026-08"]
    aug = m[1]
    assert aug["champion"]["team"] == "frozen-gradient"  # late-joiner has 2 rounds: not eligible
    assert aug["best_model"]["team"] == "baseline-gfs"
    assert [p["team"] for p in aug["podium"]] == ["frozen-gradient", "house-gbm", "baseline-gfs"]


def test_powder_accuracy_pools_rounds_instead_of_averaging_ratios():
    # a storm round forecast well, and a dry round where climatology is nearly
    # perfect and the team is off by a tenth of an inch: per-round scores are
    # +50 and -900, whose mean (-425) says nothing about the forecaster
    rows = pd.DataFrame([
        {"team": "t", "round": "2026-11-01", "powder_score": 50.0, "coverage": 1.0, "mae": 1.0,
         "n_skill": 100, "mae_skill": 1.0, "mae_climo": 2.0},
        {"team": "t", "round": "2026-11-02", "powder_score": -900.0, "coverage": 1.0, "mae": 0.1,
         "n_skill": 100, "mae_skill": 0.1, "mae_climo": 0.01},
    ])
    board = leaderboard.aggregate(rows, min_rounds=1)
    assert board.loc[0, "powder_score"] == round(100 * (1 - 110 / 201), 3)  # 45.274


def test_pooling_weights_rounds_by_station_horizons_scored():
    rows = pd.DataFrame([
        {"team": "t", "round": "a", "powder_score": 0.0, "coverage": 1.0, "mae": 2.0, "n_skill": 300, "mae_skill": 2.0, "mae_climo": 2.0},
        {"team": "t", "round": "b", "powder_score": 100.0, "coverage": 1.0, "mae": 0.0, "n_skill": 100, "mae_skill": 0.0, "mae_climo": 2.0},
    ])
    assert leaderboard.aggregate(rows, min_rounds=1).loc[0, "powder_score"] == 25.0

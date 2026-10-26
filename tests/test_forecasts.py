"""The map's forecast file: shape and ordering (works on whatever the repo holds)."""

from powderbench.forecasts import build_forecasts
from powderbench.leagues import load_leagues


def test_forecast_files_shape():
    for league in load_leagues():
        f = build_forecasts(league)
        assert f["league"] == league.name
        scores = [t["score"] for t in f["teams"] if t["score"] is not None]
        assert scores == sorted(scores, reverse=True)  # best first
        assert all(t["team"] not in ("baseline-zeros", "baseline-persistence") for t in f["teams"])
        cur = f["current"]
        if cur:
            assert cur["status"] in ("open", "locked") and len(cur["target_days"]) == 3
            for rows in list(cur["rows"].values())[:50]:
                for vals in rows.values():
                    assert len(vals) == 6
        last = f["last"]
        if last:
            assert set(last["rows"]) <= set(last["truth"])

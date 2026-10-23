"""Baseline roster: partial-range models and the consensus median."""

from datetime import date, timedelta

import pandas as pd

from powderbench import baselines


def _pred(vals):
    return pd.DataFrame([{"station_id": "s", "horizon_h": h, "snowfall_in": v} for h, v in zip((24, 48, 72), vals)])


def test_consensus_is_median_of_nwp_only_and_needs_three():
    subs = {
        "baseline-zeros": _pred([0, 0, 0]),  # not a model: excluded
        "baseline-climatology": _pred([9, 9, 9]),  # not a model: excluded
        "baseline-ecmwf": _pred([1, 2, 3]),
        "baseline-icon": _pred([3, 4, 5]),
        "baseline-gem": _pred([2, 8, 4]),
        "baseline-hrrr": _pred([10, 10]),  # 48h range: only h24/h48
    }
    c = baselines.consensus_prediction(subs).set_index("horizon_h")["snowfall_in"]
    assert c[24] == 2.5 and c[48] == 6.0  # median of 4 models
    assert c[72] == 4.0  # median of the 3 models that reach 72h
    two = {k: v for k, v in subs.items() if k in ("baseline-ecmwf", "baseline-icon")}
    assert baselines.consensus_prediction(two).empty


def test_openmeteo_prediction_keeps_horizons_within_model_range(monkeypatch):
    d = date(2026, 1, 10)
    daily = pd.DataFrame({"station_id": "s", "date": [d, d + timedelta(days=1), d + timedelta(days=2)],
                          "snowfall_in": [1.0, 2.0, None]})  # 48h model
    monkeypatch.setattr(baselines, "load_stations", lambda *a, **k: [])
    monkeypatch.setattr(baselines.openmeteo, "forecast_daily_snowfall", lambda *a, **k: daily)
    out = baselines.openmeteo_prediction(type("L", (), {"name": "x"})(), d, "ncep_hrrr_conus")
    assert out.set_index("horizon_h")["snowfall_in"].to_dict() == {24: 1.0, 48: 3.0}


def test_house_blend_learns_bias_without_lookahead(tmp_path, monkeypatch):
    """The blend shrinks a model that has run 2x high toward the truth, using
    only rounds resolved before it runs."""
    import json
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path

    from powderbench import stations as stations_mod
    from powderbench.agents.blend import blend_agent
    from powderbench.leagues import get_league

    data = Path(__file__).resolve().parents[1] / "data"
    for n in ("stations.yaml", "leagues.yaml"):
        shutil.copy(data / n, tmp_path / n)
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    stations_mod.clear_cache()
    try:
        lg = get_league("era5")
        sid = "portillo:CL:ERA5"
        for i in range(40):
            d = date(2026, 7, 1) + timedelta(days=i)
            sub = tmp_path / "submissions" / "era5" / d.isoformat(); sub.mkdir(parents=True)
            rd = tmp_path / "rounds" / "era5" / d.isoformat(); rd.mkdir(parents=True)
            truth = 2.0 + (i % 5)
            pd.DataFrame([{"station_id": sid, "horizon_h": 24, "snowfall_in": 2 * truth}]).to_csv(sub / "baseline-gfs.csv", index=False)
            pd.DataFrame([{"station_id": sid, "horizon_h": 24, "truth_in": truth, "valid": True}]).to_csv(rd / "truth.csv", index=False)
        target = date(2026, 8, 20)
        sub = tmp_path / "submissions" / "era5" / target.isoformat(); sub.mkdir(parents=True)
        pd.DataFrame([{"station_id": sid, "horizon_h": 24, "snowfall_in": 10.0}]).to_csv(sub / "baseline-gfs.csv", index=False)
        out = blend_agent(lg, target, lg.cutoff_utc(target)).set_index("station_id")
        assert 5.0 <= out.loc[sid, "snowfall_in"] < 7.0  # learned ~0.5x, shrunk slightly toward 1
        # nothing resolved yet -> no correction
        early = blend_agent(lg, target, datetime(2026, 7, 2, tzinfo=timezone.utc)).set_index("station_id")
        assert early.loc[sid, "snowfall_in"] == 10.0
    finally:
        monkeypatch.delenv("POWDERBENCH_DATA_DIR")
        stations_mod.clear_cache()

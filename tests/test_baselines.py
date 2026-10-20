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

"""SWE and density tracks: SNOTEL SWE truth windows and their scoring."""

from datetime import date

import pandas as pd
import pytest

from powderbench import scoring, truth


def _obs():
    days = pd.date_range("2026-01-01", periods=5).date
    return pd.DataFrame({"station_id": "A", "date": days,
                         "SNWD": [10, 18, 18, 30, 30], "WTEQ": [5.0, 5.8, 5.8, 7.0, 7.0]})


def test_swe_truth_windows():
    d = truth.daily_snowfall(_obs())
    assert d.set_index("date").loc[date(2026, 1, 2), "swe24"] == pytest.approx(0.8)
    w = truth.window_truth(d, date(2026, 1, 2)).set_index("horizon_h")
    assert w.loc[24, "truth_in"] == 8 and w.loc[24, "truth_swe_in"] == pytest.approx(0.8)
    assert w.loc[72, "truth_swe_in"] == pytest.approx(2.0)


def test_swe_and_density_scoring():
    truth_df = pd.DataFrame({"station_id": list("ABCDEF"), "horizon_h": 24, "truth_in": [8.0] * 6,
                             "valid": True, "truth_swe_in": [0.8] * 6})  # 10% density
    climo = truth_df[["station_id", "horizon_h"]].assign(snowfall_in=0.0)
    perfect = truth_df[["station_id", "horizon_h"]].assign(snowfall_in=8.0, swe_in=0.8)
    m = scoring.score_round(perfect, truth_df, climo)
    assert m["swe_accuracy"] == pytest.approx(100.0)
    assert m["density_mae"] == pytest.approx(0.0)
    no_swe = truth_df[["station_id", "horizon_h"]].assign(snowfall_in=8.0)
    m = scoring.score_round(no_swe, truth_df, climo)
    assert m["swe_accuracy"] is None and m["density_accuracy"] is None

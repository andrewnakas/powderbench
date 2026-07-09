from datetime import date

import pandas as pd

from powderbench import truth


def obs(rows):
    return pd.DataFrame(rows, columns=["station_id", "date", "SNWD", "WTEQ"])


D = [date(2025, 1, i) for i in range(1, 8)]


def test_snow24_is_positive_depth_delta():
    df = truth.daily_snowfall(
        obs([("s", D[0], 50, 10.0), ("s", D[1], 62, 11.2), ("s", D[2], 58, 11.2)])
    )
    by_date = df.set_index("date")
    assert by_date.loc[D[1], "snow24"] == 12  # storm day
    assert by_date.loc[D[1], "valid"]
    assert by_date.loc[D[2], "snow24"] == 0  # settling clamps to zero
    assert by_date.loc[D[2], "valid"]


def test_missing_reading_voids_day():
    df = truth.daily_snowfall(
        obs([("s", D[0], 50, 10.0), ("s", D[1], None, 10.5), ("s", D[2], 55, 10.8)])
    )
    by_date = df.set_index("date")
    assert not by_date.loc[D[1], "valid"]
    assert by_date.loc[D[1], "qc_flag"] == truth.QC_MISSING
    assert not by_date.loc[D[2], "valid"]  # previous-day reading also missing


def test_date_gap_voids_day():
    df = truth.daily_snowfall(obs([("s", D[0], 50, 10.0), ("s", D[3], 60, 11.0)]))
    assert not df["valid"].any()


def test_big_jump_without_swe_support_is_voided():
    df = truth.daily_snowfall(
        obs([("s", D[0], 50, 10.0), ("s", D[1], 70, 10.02)])  # 20" jump, SWE flat
    )
    row = df.iloc[0]
    assert not row["valid"]
    assert row["qc_flag"] == truth.QC_NO_SWE_SUPPORT


def test_big_jump_with_swe_support_is_kept():
    df = truth.daily_snowfall(obs([("s", D[0], 50, 10.0), ("s", D[1], 70, 11.5)]))
    row = df.iloc[0]
    assert row["valid"] and row["snow24"] == 20


def test_big_jump_with_missing_swe_is_voided():
    df = truth.daily_snowfall(obs([("s", D[0], 50, None), ("s", D[1], 70, None)]))
    assert df.iloc[0]["qc_flag"] == truth.QC_SWE_MISSING


def test_small_jump_needs_no_swe_support():
    df = truth.daily_snowfall(obs([("s", D[0], 50, None), ("s", D[1], 53, None)]))
    row = df.iloc[0]
    assert row["valid"] and row["snow24"] == 3


def test_implausible_jump_voided():
    df = truth.daily_snowfall(obs([("s", D[0], 50, 10.0), ("s", D[1], 120, 20.0)]))
    assert df.iloc[0]["qc_flag"] == truth.QC_IMPLAUSIBLE


def test_window_truth_cumulative_and_validity():
    daily = truth.daily_snowfall(
        obs(
            [
                ("s", D[0], 50, 10.0),
                ("s", D[1], 55, 10.5),  # 5" on Jan 2
                ("s", D[2], 58, 10.8),  # 3" on Jan 3
                ("s", D[3], 58, 10.8),  # 0" on Jan 4
                ("s", D[4], None, None),  # Jan 5 void
            ]
        )
    )
    w = truth.window_truth(daily, target_day=D[1])
    w = w.set_index("horizon_h")
    assert w.loc[24, "truth_in"] == 5
    assert w.loc[48, "truth_in"] == 8
    assert w.loc[72, "truth_in"] == 8
    # windows touching the voided day are invalid
    w2 = truth.window_truth(daily, target_day=D[2]).set_index("horizon_h")
    assert w2.loc[24, "valid"] and w2.loc[48, "valid"]
    assert not w2.loc[72, "valid"]

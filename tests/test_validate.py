import pandas as pd
import pytest

from powderbench import HORIZONS
from powderbench.stations import station_ids
from powderbench.validate import validate_submission


def write_csv(tmp_path, df):
    p = tmp_path / "sub.csv"
    df.to_csv(p, index=False)
    return p


def full_submission():
    rows = [
        {"station_id": s, "horizon_h": h, "snowfall_in": 1.0}
        for s in station_ids()
        for h in HORIZONS
    ]
    return pd.DataFrame(rows)


def test_valid_full_submission(tmp_path):
    res = validate_submission(write_csv(tmp_path, full_submission()))
    assert res.ok and res.coverage == 1.0 and not res.warnings


def test_missing_column_fails(tmp_path):
    res = validate_submission(write_csv(tmp_path, full_submission().drop(columns=["snowfall_in"])))
    assert not res.ok


def test_unknown_station_fails(tmp_path):
    df = full_submission()
    df.loc[0, "station_id"] = "999:XX:SNTL"
    res = validate_submission(write_csv(tmp_path, df))
    assert not res.ok and "unknown station" in res.errors[0]


def test_bad_horizon_fails(tmp_path):
    df = full_submission()
    df.loc[0, "horizon_h"] = 36
    assert not validate_submission(write_csv(tmp_path, df)).ok


def test_negative_snowfall_fails(tmp_path):
    df = full_submission()
    df.loc[0, "snowfall_in"] = -1
    assert not validate_submission(write_csv(tmp_path, df)).ok


def test_duplicate_rows_fail(tmp_path):
    df = pd.concat([full_submission(), full_submission().head(1)])
    assert not validate_submission(write_csv(tmp_path, df)).ok


def test_low_coverage_warns_but_passes(tmp_path):
    res = validate_submission(write_csv(tmp_path, full_submission().head(10)))
    assert res.ok and res.warnings and res.coverage < 0.7


def test_decreasing_quantiles_fail(tmp_path):
    df = full_submission()
    for c, v in [("p10", 5.0), ("p25", 4.0), ("p50", 3.0), ("p75", 2.0), ("p90", 1.0)]:
        df[c] = v
    res = validate_submission(write_csv(tmp_path, df))
    assert not res.ok and "non-decreasing" in " ".join(res.errors)


def test_prob_out_of_range_fails(tmp_path):
    df = full_submission()
    df["prob_6in"] = 1.5
    assert not validate_submission(write_csv(tmp_path, df)).ok

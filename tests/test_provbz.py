"""South Tyrol adapter: CET/CEST stamps, season fetch, depth-rise truth."""

from datetime import date

import pandas as pd

from powderbench.networks import provbz

RECORDS = [  # newest first, as the API returns them
    {"DATE": "2026-01-11T00:10:00CET", "VALUE": 70.4},
    {"DATE": "2026-01-11T00:00:00CET", "VALUE": 70.0},
    {"DATE": "2026-01-10T12:00:00CET", "VALUE": 60.0},
    {"DATE": "2026-01-10T00:00:00CET", "VALUE": 50.0},
    {"DATE": "2026-01-09T23:50:00CET", "VALUE": 50.2},
    {"DATE": "2026-01-12T00:00:00CET", "VALUE": -1.0},
]


def test_parse_series_zones():
    df = provbz.parse_series([{"DATE": "2025-10-01T00:00:00CEST", "VALUE": 3.0},
                              {"DATE": "2026-01-10T00:00:00CET", "VALUE": 50.0}])
    assert list(df["timestamp"]) == [pd.Timestamp("2025-09-30T22:00Z"), pd.Timestamp("2026-01-09T23:00Z")]
    assert provbz.parse_series([]).empty


def test_daily_rises(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    calls = []

    def fake_get(path, **params):
        calls.append(params)
        return RECORDS if params["date_from"] == "20250901" else []

    monkeypatch.setattr(provbz, "_get", fake_get)
    t = provbz.daily(["61690SF:IT:PROVBZ"], date(2026, 1, 10), date(2026, 1, 11)).set_index("date")
    # local midnight 10th -> 11th: 50.1 (median of 50.2 and 50.0) -> 70.2 (median of 70.0 and 70.4)
    assert abs(t.loc[date(2026, 1, 10), "snow24"] - 20.1 / 2.54) < 1e-3
    assert not t.loc[date(2026, 1, 11), "valid"]  # negative reading is missing, not zero
    # one request for the season, then the cache
    provbz.daily(["61690SF:IT:PROVBZ"], date(2026, 1, 10), date(2026, 1, 11))
    assert len(calls) == 1 and calls[0]["sensor_code"] == "HS"

"""ARPA Lombardia adapter: midnight window, validity flags, dataset choice, depth-rise truth."""

from datetime import date

from powderbench.networks import arpal

RECORDS = [
    {"data": "2026-01-09T22:50:00.000", "valore": "80", "stato": "VA"},  # outside the window
    {"data": "2026-01-09T23:00:00.000", "valore": "50", "stato": "VA"},
    {"data": "2026-01-09T23:50:00.000", "valore": "-999", "stato": "NA"},
    {"data": "2026-01-10T00:00:00.000", "valore": "51", "stato": "VA"},
    {"data": "2026-01-10T00:10:00.000", "valore": "52", "stato": "VA"},
    {"data": "2026-01-10T23:10:00.000", "valore": "60", "stato": "VA"},
    {"data": "2026-01-11T00:00:00.000", "valore": "61", "stato": "VA"},
    {"data": "2026-01-11T23:00:00.000", "valore": "62", "stato": "VA"},
]


def test_midnight_depths():
    s = arpal.midnight_depths(RECORDS)
    assert s[date(2026, 1, 10)] == 51.0  # 23:00 D-1 .. 00:50 D; the NA reading and 22:50 are dropped
    assert s[date(2026, 1, 11)] == 60.5
    assert s[date(2026, 1, 12)] == 62.0


def test_dataset_choice():
    today = date(2026, 11, 19)
    assert arpal._datasets(date(2009, 9, 30), date(2010, 10, 1), today) == ["5kig-ayub"]
    assert arpal._datasets(date(2010, 9, 30), date(2011, 10, 1), today) == ["5kig-ayub", "id9e-7wf3"]
    assert arpal._datasets(date(2025, 9, 30), date(2026, 10, 1), today) == ["uqbu-tt6m", "647i-nhxk"]


def test_daily_rises_and_deadband(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(arpal, "_get", lambda ds, sensor, b, e: RECORDS if ds == "uqbu-tt6m" else [])
    t = arpal.daily(["8017:IT:ARPAL"], date(2026, 1, 10), date(2026, 1, 12)).set_index("date")
    assert abs(t.loc[date(2026, 1, 10), "snow24"] - 9.5 / 2.54) < 1e-3
    assert t.loc[date(2026, 1, 11), "snow24"] == 0.0  # +1.5 cm is under the 2 cm deadband
    assert not t.loc[date(2026, 1, 12), "valid"]

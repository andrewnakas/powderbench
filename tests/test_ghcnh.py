"""GHCNh adapter: morning hour, spike filter, morning-to-morning rises."""

from datetime import date

import pandas as pd

from powderbench.networks import ghcnh


def _rep(rows):
    return pd.DataFrame({"time": pd.to_datetime([t for t, _ in rows]), "depth_cm": [v for _, v in rows]})


def test_depth_rows_mm_to_cm_and_bad_codes():
    df = pd.DataFrame({"DATE": ["2026-01-01T06:00:00", "2026-01-01T07:00:00", "2026-01-01T18:00:00",
                                "2026-01-02T06:00:00", "2026-01-02T06:30:00"],
                       "snow_depth": [990.0, None, 1000.0, 1010.0, 5.0],
                       "snow_depth_Quality_Code": [None, None, "3", "1", None],
                       "snow_depth_Source_Code": ["220"] * 5})
    out = ghcnh.depth_rows(df)
    assert out["time"].dt.hour.tolist() == [6, 6]  # the 07 UTC blank, the code-3 row and the :30 row go
    assert out["depth_cm"].tolist() == [99.0, 101.0]


def test_morning_hour_by_local_time():
    t = pd.Series(pd.to_datetime(["2026-01-01T03:00", "2026-01-02T03:00", "2026-01-01T00:00",
                                  "2026-01-01T18:00", "2026-01-02T18:00", "2026-01-03T18:00"]))
    # Almaty (77 E): 03 UTC is 08 local; 18 UTC (most reports) is the evening
    assert ghcnh.morning_hour(t, 77.0) == 3
    # Tatras (20 E): 06 UTC would win, absent here; 03 UTC is 04:20 local, too early
    assert ghcnh.morning_hour(t, 20.0) is None


def test_spike_needs_no_neighbour_and_storm_is_confirmed_by_the_next_reading():
    rep = _rep([("2025-12-31T18:00", 99.0), ("2026-01-01T06:00", 1.0), ("2026-01-01T18:00", 99.0),
                ("2026-01-02T06:00", 101.0), ("2026-01-02T18:00", 160.0), ("2026-01-03T06:00", 170.0),
                ("2026-01-03T18:00", 165.0)])
    ok = ghcnh.confirmed(rep["depth_cm"]).tolist()
    # the first reading's only neighbour is the junk one: dropped too (window edges are conservative)
    assert ok == [False, False, True, True, True, True, True]
    md = ghcnh.morning_depths(rep, 20.0)
    assert date(2026, 1, 1) not in md.index  # the 1 cm junk on a 1 m pack
    assert md[date(2026, 1, 3)] == 170.0


def test_daily_rises(monkeypatch):
    rep = _rep([("2026-01-01T06:00", 50.0), ("2026-01-02T06:00", 50.5), ("2026-01-03T06:00", 75.5),
                ("2026-01-04T06:00", 70.0), ("2026-01-05T06:00", 71.0)])
    monkeypatch.setattr(ghcnh, "reports", lambda gid, b, e: rep)
    t = ghcnh.daily(["LOM00011930:GHCNH"], date(2026, 1, 1), date(2026, 1, 5), {"LOM00011930:GHCNH": 20.2})
    t = t.set_index("date")
    assert t.loc[date(2026, 1, 1), "snow24"] == 0.0  # 0.5 cm: under the 1 cm deadband
    assert abs(t.loc[date(2026, 1, 2), "snow24"] - 25 / 2.54) < 1e-3
    assert t.loc[date(2026, 1, 3), "snow24"] == 0.0  # settling
    assert abs(t.loc[date(2026, 1, 4), "snow24"] - 1 / 2.54) < 1e-3
    assert not t.loc[date(2026, 1, 5), "valid"]  # no reading on the 6th yet


def test_bare_ground_fill():
    nan = float("nan")
    rep = _rep([("2025-11-01T06:00", nan), ("2025-11-02T06:00", nan),  # bare season: no depth for weeks
                ("2025-11-03T06:00", 10.0), ("2025-11-04T06:00", 8.0), ("2025-11-05T06:00", 4.0),
                ("2025-11-06T06:00", nan),  # melted out: last confirmed 4 cm a day ago
                ("2025-11-07T06:00", 6.0), ("2025-11-08T06:00", 30.0), ("2025-11-09T06:00", 28.0),
                ("2025-11-10T06:00", nan),  # a report without depth under a 28 cm pack: missing
                ("2025-11-11T06:00", 27.0)])
    md = ghcnh.morning_depths(rep, 20.0)
    assert md[date(2025, 11, 1)] == 0.0 and md[date(2025, 11, 2)] == 0.0
    assert md[date(2025, 11, 6)] == 0.0
    assert date(2025, 11, 10) not in md.index


def test_no_fill_right_before_a_deep_reading():
    nan = float("nan")
    rep = _rep([("2026-01-01T06:00", nan), ("2026-01-02T06:00", 150.0), ("2026-01-03T06:00", 148.0)])
    md = ghcnh.morning_depths(rep, 20.0)
    assert date(2026, 1, 1) not in md.index  # 0 -> 150 cm overnight is a gap, not a storm


def test_morning_hour_prefers_the_earliest_well_reported():
    t = pd.Series(pd.to_datetime([f"2026-01-{d:02d}T{h:02d}:00" for d in range(1, 11) for h in (6, 9)]
                                 + ["2026-01-11T09:00", "2026-01-12T09:00"]))
    assert ghcnh.morning_hour(t, 20.0) == 6  # 10 reports at 06 vs 12 at 09: 06 is within 80%


def test_zero_dropouts_inside_a_pack():
    md = pd.Series([100.0, 0.0, 0.0, 105.0, 3.0, 0.0, 0.0],
                   index=[date(2026, 1, d) for d in (1, 2, 3, 4, 20, 21, 22)])
    assert ghcnh.dropouts(md).tolist() == [False, True, True, False, False, False, False]


def test_no_fill_in_years_without_depth_reports():
    nan = float("nan")
    rows = [(f"2005-01-{d:02d}T06:00", nan) for d in range(1, 31)]  # the station sends no depth group at all
    md = ghcnh.morning_depths(_rep(rows + [("2026-01-01T06:00", 40.0), ("2026-01-02T06:00", 41.0)]), 10.7)
    assert all(d.year == 2026 for d in md.index)


def test_units_and_codes_by_source():
    df = pd.DataFrame({"DATE": [f"2026-01-0{d}T06:00:00" for d in range(1, 8)],
                       "snow_depth": [60.0, -1.0, 9970.0, 9980.0, 9990.0, 4.0, 610.0],
                       "snow_depth_Quality_Code": [None] * 7,
                       "snow_depth_Source_Code": ["398", "398", "220", "220", "220", "395", "220"]})
    out = ghcnh.depth_rows(df).set_index("time")["depth_cm"]
    assert out.iloc[0] == 60.0 and out.iloc[1] == 0.0  # MET Norway: cm, -1 = no snow
    assert out.iloc[2] == 0.0 and out.iloc[3] == 0.0  # < 0.5 cm, patchy
    assert out.iloc[4] != out.iloc[4] and out.iloc[5] != out.iloc[5]  # not measurable; IMGW filler
    assert out.iloc[6] == 61.0


def test_dry_autumn_mornings_read_0_after_a_reporting_winter():
    nan = float("nan")
    rows = [("2026-03-01T06:00", 50.0), ("2026-03-02T06:00", 49.0)]  # last winter's reports
    rows += [(f"2026-11-{d:02d}T06:00", nan) for d in range(1, 11)]  # autumn: reports, no depth group
    md = ghcnh.morning_depths(_rep(rows), 20.0)
    assert all(md[date(2026, 11, d)] == 0.0 for d in range(1, 11))

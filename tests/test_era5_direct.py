from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from powderbench.nwp import era5


def _st(sid, lat, lon, elev_ft=8000.0):
    return SimpleNamespace(station_id=sid, latitude=lat, longitude=lon, elevation_ft=elev_ft)


def test_nearby_stations_share_one_request_box():
    a, b, c = _st("a", 39.6, -106.4), _st("b", 39.5, -106.0), _st("c", 47.0, -121.4)
    groups = era5.clusters([a, b, c])
    assert sorted(len(g) for g in groups) == [1, 2]
    box = era5.area(next(g for g in groups if len(g) == 2), era5.PRODUCTS["era5"])
    assert box == [40.25, -107.0, 39.0, -105.5]  # N W S E, padded and snapped to 0.25
    land = era5.area([a], era5.PRODUCTS["era5-land"])
    assert land == [40.1, -106.9, 39.1, -105.9]


def test_era5_land_running_totals_become_hourly_amounts():
    idx = pd.date_range("2020-01-01 01:00", periods=26, freq="h", tz="UTC")
    hourly = np.arange(1, 27, dtype=float) / 10  # what fell in each hour
    running, total = [], 0.0
    for ts, v in zip(idx, hourly):
        total = v if ts.hour == 1 else total + v
        running.append(total)
    raw = pd.DataFrame({"sf": running, "t2": -5.0}, index=idx)
    got = era5.hourly_snow_mm(raw, era5.PRODUCTS["era5-land"])
    assert np.allclose(got.to_numpy()[1:], hourly[1:])  # the first row has no previous value
    plain = era5.hourly_snow_mm(pd.DataFrame({"sf": hourly, "t2": 0.0}, index=idx), era5.PRODUCTS["era5"])
    assert np.allclose(plain.to_numpy(), hourly)


def test_elevation_rule_only_where_the_cell_is_far_off_the_station():
    idx = pd.date_range("2020-01-01 01:00", periods=3, freq="h", tz="UTC")
    snow, t2 = pd.Series([1.0, 1.0, 1.0], index=idx), pd.Series([-1.0, 1.0, 3.0], index=idx)
    assert era5.apply_rule(snow, t2, 50.0).tolist() == [1.0, 1.0, 1.0]
    # cell 400 m below the station: 2.6 C colder there; 1 -> -1.6 C, 3 -> 0.4 C
    got = era5.apply_rule(snow, t2, -400.0).to_numpy()
    assert got[0] == 1.0 and got[1] == 1.0 and 0.75 < got[2] < 0.85


def test_local_days_follow_the_station_zone():
    idx = pd.date_range("2020-01-01 00:00", "2020-01-03 12:00", freq="h", tz="UTC")
    snow = pd.Series(0.0, index=idx)
    snow[pd.Timestamp("2020-01-02 03:00", tz="UTC")] = 10.0  # 20:00 MST on Jan 1
    got = era5.local_daily(snow, ZoneInfo("America/Denver"), date(2020, 1, 1), date(2020, 1, 3))
    assert got[date(2020, 1, 1)] == pytest.approx(10.0)
    assert got[date(2020, 1, 2)] == pytest.approx(0.0)
    assert date(2020, 1, 3) not in got  # not fully covered


def test_requests_are_stable_for_the_cache():
    prod = era5.PRODUCTS["era5"]
    r1 = era5.request(prod, ["snowfall", "2m_temperature"], 1991, [1, 2], [40.0, -107.0, 39.0, -106.0])
    r2 = era5.request(prod, ["snowfall", "2m_temperature"], 1991, [1, 2], [40.0, -107.0, 39.0, -106.0])
    assert era5._key(prod, r1) == era5._key(prod, r2)
    assert r1["product_type"] == ["reanalysis"] and len(r1["day"]) == 31 and len(r1["time"]) == 24
    assert "product_type" not in era5.request(era5.PRODUCTS["era5-land"], ["snowfall"], 1991, [1], r1["area"])

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
    got = era5.hourly_snow_mm(pd.Series(running, index=idx), era5.PRODUCTS["era5-land"])
    assert np.allclose(got.to_numpy()[1:], hourly[1:])  # the first row has no previous value
    plain = era5.hourly_snow_mm(pd.Series(hourly, index=idx), era5.PRODUCTS["era5"])
    assert np.allclose(plain.to_numpy(), hourly)


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


def test_archive_files_cover_a_range_in_order(monkeypatch):
    from datetime import datetime, timezone

    from powderbench.nwp import omdata

    monkeypatch.setattr(omdata, "meta", lambda d: {"chunk_time_length": 504})
    monkeypatch.setattr(omdata, "listing", lambda d, v: frozenset({"year_2021.om", "chunk_904.om", "chunk_905.om"}))
    utc = timezone.utc
    # the year file runs to its end; chunk 904 (904 x 504 h after 1970-01-01 =
    # 2021-12-23 00Z) takes over from 2022-01-01, 9 days into it
    got = list(omdata._pieces("x", "v", datetime(2021, 12, 20, tzinfo=utc), datetime(2022, 1, 20, tzinfo=utc)))
    assert [p[0] for p in got] == ["year_2021.om", "chunk_904.om", "chunk_905.om"]
    assert got[0][1] == (datetime(2021, 12, 20, tzinfo=utc) - datetime(2021, 1, 1, tzinfo=utc)).days * 24
    assert got[0][2] == 12 * 24 and got[1][1] == 9 * 24 and got[1][2] == 504 - 9 * 24
    assert sum(p[2] for p in got) == 31 * 24
    with pytest.raises(FileNotFoundError):
        list(omdata._pieces("x", "v", datetime(1999, 1, 1, tzinfo=utc), datetime(1999, 1, 2, tzinfo=utc)))


def test_era5_source_switch(monkeypatch):
    from powderbench import openmeteo

    calls = []
    monkeypatch.setattr(era5, "daily_snowfall", lambda st, b, e, source=None: calls.append(source) or "direct")
    monkeypatch.setattr(openmeteo, "_fetch", lambda *a, **k: "api")
    monkeypatch.delenv("PB_ERA5_SOURCE", raising=False)
    assert openmeteo.era5_daily_snowfall([], date(2020, 1, 1), date(2020, 1, 2)) == "api"
    monkeypatch.setenv("PB_ERA5_SOURCE", "s3")
    assert openmeteo.era5_daily_snowfall([], date(2020, 1, 1), date(2020, 1, 2)) == "direct" and calls == ["s3"]


def test_cells_are_kept_as_places_and_found_again_in_any_box():
    from powderbench.nwp import grib

    world = {"gridType": "regular_ll", "Ni": 1440, "Nj": 721, "jScansPositively": 0, "iScansNegatively": 0,
             "jPointsAreConsecutive": 0, "latitudeOfFirstGridPointInDegrees": 90.0,
             "longitudeOfFirstGridPointInDegrees": 0.0, "iDirectionIncrementInDegrees": 0.25,
             "jDirectionIncrementInDegrees": 0.25}
    idx = int(grib.point_index(world, np.array([-41.0]), np.array([-71.75]))[0])
    assert era5.cell_latlon(world, idx) == (-41.0, -71.75)
    box = dict(world, Ni=9, Nj=9, latitudeOfFirstGridPointInDegrees=-40.0, longitudeOfFirstGridPointInDegrees=-72.5)
    j, i = divmod(int(grib.point_index(box, np.array([-41.0]), np.array([-71.75]))[0]), 9)
    assert (j, i) == (4, 3)

from datetime import datetime, timezone

import numpy as np
import pytest

from powderbench.nwp import omdata, sources

RUN = datetime(2026, 11, 17, tzinfo=timezone.utc)


def _grid(ni=4, nj=4, lat0=45.0, lon0=6.0, step=1.0, height=1000.0):
    g = {"gridType": "regular_ll", "Ni": ni, "Nj": nj,
         "latitudeOfFirstGridPointInDegrees": lat0, "longitudeOfFirstGridPointInDegrees": lon0,
         "iDirectionIncrementInDegrees": step, "jDirectionIncrementInDegrees": step,
         "jScansPositively": 1, "iScansNegatively": 0, "jPointsAreConsecutive": 0}
    return g, np.full(ni * nj, height), np.ones(ni * nj)


@pytest.fixture
def archive(monkeypatch):
    """Two fake domains: 'fine' (6 hourly steps, covers lat 45-48) and
    'coarse' (12 hourly steps, everywhere), with per-domain variables."""
    doms = {"fine": {"grid": _grid(), "hours": np.arange(7.0), "vars": {}},
            "coarse": {"grid": _grid(ni=10, nj=10, lat0=40.0, lon0=0.0), "hours": np.arange(13.0), "vars": {}}}
    monkeypatch.setattr(omdata, "grid", lambda d: doms[d]["grid"])
    monkeypatch.setattr(omdata, "run_hours", lambda d, r: doms[d]["hours"])
    monkeypatch.setattr(omdata, "run_variables", lambda d, r: frozenset(doms[d]["vars"]))
    monkeypatch.setattr(omdata, "run_values",
                        lambda d, r, v, cells: np.tile(doms[d]["vars"][v], (len(cells), 1)).astype(float))
    return doms


def test_seamless_continues_past_the_fine_domain(archive):
    archive["fine"]["vars"] = {"snowfall_water_equivalent": [np.nan, 1, 1, 1, 1, 1, 1], "temperature_2m": [-5.0] * 7}
    archive["coarse"]["vars"] = {"snowfall_water_equivalent": [np.nan] + [2.0] * 12, "temperature_2m": [-5.0] * 13}
    src = sources.OpenMeteoArchive("t", "t", ["fine", "coarse"], online_h=1)
    # one station inside both domains, one only in the coarse one
    acc = src.accum(RUN, [0, 6, 12], [46.0, 41.0], [7.0, 1.0], [1000.0, 1000.0])
    assert acc[6].tolist() == [6.0, 12.0]
    assert acc[12].tolist() == [6.0 + 12.0, 24.0]  # fine to hour 6, then the coarse domain's increments
    assert src.steps(RUN) == list(range(13))


def test_snow_from_rain_or_from_cold_precipitation(archive):
    archive["coarse"]["vars"] = {"precipitation": [np.nan] + [3.0] * 12, "rain": [np.nan] + [1.0] * 12,
                                 "temperature_2m": [5.0] * 13}
    src = sources.OpenMeteoArchive("t", "t", ["coarse"], online_h=1)
    assert src.accum(RUN, [12], [41.0], [1.0], [1000.0])[12][0] == 24.0  # (3 - 1) x 12
    # precipitation only (AROME HD, JMA): snow while the temperature is below 0 C
    archive["coarse"]["vars"] = {"precipitation": [np.nan] + [1.0] * 12,
                                 "temperature_2m": [-1.0] * 7 + [3.0] * 6}
    got = src.accum(RUN, [6, 12], [41.0], [1.0], [1000.0])
    assert got[6][0] == 6.0 and got[12][0] == pytest.approx(6.0 + 0.25)  # -1 -> 3 C: a quarter below zero


def test_elevation_rule_and_open_meteo_snowfall_in_cm(archive):
    archive["coarse"]["vars"] = {"snowfall": [np.nan] + [0.7] * 12, "temperature_2m": [1.0] * 13}
    src = sources.OpenMeteoArchive("t", "t", ["coarse"], online_h=1)
    # cell 1000 m, station at 1050 m: within 100 m, no rule -> 1 mm a step
    assert src.accum(RUN, [12], [41.0], [1.0], [1050.0])[12][0] == pytest.approx(12.0)
    # cell 1000 m above the station: 6.5 K warmer there, the snow is dropped
    assert src.accum(RUN, [12], [41.0], [1.0], [0.0])[12][0] == 0.0
    # station 1000 m above the cell: -5.5 C, all of it kept
    assert src.accum(RUN, [12], [41.0], [1.0], [2000.0])[12][0] == pytest.approx(12.0)


def test_station_outside_every_domain_is_missing(archive):
    archive["fine"]["vars"] = {"snowfall_water_equivalent": [np.nan] + [1.0] * 6, "temperature_2m": [-5.0] * 7}
    src = sources.OpenMeteoArchive("t", "t", ["fine"], online_h=1)
    got = src.accum(RUN, [6], [10.0], [100.0], [1000.0])
    assert np.isnan(got[6][0])


def test_amounts_stored_per_interval_line_up_with_valid_times(archive):
    # the archive keeps N - 1 amounts (intervals ending at hours 1..N-1) and N temperatures
    archive["coarse"]["vars"] = {"snowfall_water_equivalent": [1.0] * 12, "temperature_2m": [-5.0] * 13}
    src = sources.OpenMeteoArchive("t", "t", ["coarse"], online_h=1)
    got = src.accum(RUN, [1, 12], [41.0], [1.0], [1000.0])
    assert got[1][0] == 1.0 and got[12][0] == 12.0


def test_open_meteo_rotation_round_trips():
    # Open-Meteo's inverse (Domains/RotatedLatLon.swift) undoes the ported forward
    lat_p, lon_p = -36.0885, 245.305  # GEM HRDPS
    th, ph = np.radians(90 + lat_p), np.radians(lon_p)
    fwd = omdata.rotated_forward(lat_p, lon_p)
    lats, lons = np.array([43.59, 50.07, 46.0]), np.array([-110.83, -122.89, -75.0])
    x, y = fwd(lats, lons)
    lo, la = np.radians(x), np.radians(y)
    lat2 = -np.arcsin(np.cos(th) * np.sin(la) - np.cos(lo) * np.sin(th) * np.cos(la))
    lon2 = -(np.arctan2(np.sin(lo), np.tan(la) * np.sin(th) + np.cos(lo) * np.cos(th)) - ph)
    assert np.allclose(np.degrees(lat2), lats, atol=1e-6)
    assert np.allclose((np.degrees(lon2) + 180) % 360 - 180, lons, atol=1e-6)


def test_projected_grid_indexes_through_its_own_function():
    from powderbench.nwp import grib

    g = {"gridType": "projected", "Ni": 10, "Nj": 10, "jScansPositively": 1, "iScansNegatively": 0,
         "jPointsAreConsecutive": 0, "DxInMetres": 1000.0, "DyInMetres": 1000.0,
         "ij": lambda lats, lons: (lons * 10.0, lats * 10.0)}
    assert grib.point_index(g, np.array([0.31, 2.0]), np.array([0.42, 0.0])).tolist() == [3 * 10 + 4, -1]

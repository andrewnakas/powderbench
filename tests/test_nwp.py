from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from powderbench import baselines, nwp
from powderbench.nwp import grib, sources
from powderbench.stations import Station


def _st(sid, lat, lon):
    return Station(station_id=sid, slug=sid, name=sid, resort="", region="", state="", elevation_ft=8000,
                   latitude=lat, longitude=lon)


def test_ncep_index_lengths_and_windows():
    text = ("596:100:d=2026020100:APCP:surface:18-24 hour acc fcst:\n"
            "597:250:d=2026020100:APCP:surface:0-1 day acc fcst:\n"
            "605:400:d=2026020100:CSNOW:surface:18-24 hour ave fcst:\n"
            "606:430:d=2026020100:CRAIN:surface:24 hour fcst:\n")
    rows = grib.ncep_index(text)
    assert [r["length"] for r in rows] == [150, 150, 30, None]
    assert (rows[1]["start_h"], rows[1]["end_h"], rows[1]["stat"]) == (0, 24, "acc")
    assert (rows[2]["start_h"], rows[2]["end_h"], rows[2]["stat"]) == (18, 24, "ave")
    assert "start_h" not in rows[3]
    assert sources.Gfs._pick(rows, "APCP", "acc", 24, total=True)["offset"] == 250
    assert sources.Gfs._pick(rows, "CSNOW", "ave", 24, total=False)["offset"] == 400


def test_ecmwf_index_skips_bad_lines():
    text = '{"param": "sf", "_offset": 5, "_length": 9}\nnot json\n\n{"param": "tp", "_offset": 14, "_length": 3}\n'
    assert [r["param"] for r in grib.ecmwf_index(text)] == ["sf", "tp"]


def test_water_units():
    assert grib.to_mm("m of water equivalent") == 1000.0  # IFS sf
    assert grib.to_mm("kg m**-2") == 1.0  # AIFS sf, GFS APCP
    with pytest.raises(ValueError):
        grib.to_mm("K")


def _meta(lat0, lon0, ni, nj, d=0.25):
    return {"gridType": "regular_ll", "Ni": ni, "Nj": nj, "latitudeOfFirstGridPointInDegrees": lat0,
            "longitudeOfFirstGridPointInDegrees": lon0, "iDirectionIncrementInDegrees": d,
            "jDirectionIncrementInDegrees": d, "jScansPositively": 0, "iScansNegatively": 0,
            "jPointsAreConsecutive": 0}


def test_point_index_global_grids():
    lats, lons = np.array([40.6, 61.2, -45.0]), np.array([-111.6, 12.5, 179.99])
    ecmwf = grib.point_index(_meta(90, -180, 1440, 721), lats, lons)  # IFS/AIFS: -180..179.75
    gfs = grib.point_index(_meta(90, 0, 1440, 721), lats, lons)  # GFS: 0..359.75
    for idx, lon0 in ((ecmwf, -180), (gfs, 0)):
        j, i = idx // 1440, idx % 1440
        assert np.allclose(90 - j * 0.25, np.round(lats * 4) / 4)
        got = (lon0 + i * 0.25 + 180) % 360 - 180
        want = (np.round(lons * 4) / 4 + 180) % 360 - 180
        assert np.allclose(got, want)


def test_point_index_off_regional_grid():
    idx = grib.point_index(_meta(50, -125, 100, 100), np.array([45.0, 10.0]), np.array([-120.0, 0.0]))
    assert idx[0] >= 0 and idx[1] == -1


def test_point_index_matches_eccodes_nearest():
    eccodes = pytest.importorskip("eccodes")
    gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        for k, v in (("Ni", 1440), ("Nj", 721), ("latitudeOfFirstGridPointInDegrees", 90.0),
                     ("longitudeOfFirstGridPointInDegrees", 0.0), ("latitudeOfLastGridPointInDegrees", -90.0),
                     ("longitudeOfLastGridPointInDegrees", 359.75), ("iDirectionIncrementInDegrees", 0.25),
                     ("jDirectionIncrementInDegrees", 0.25)):
            eccodes.codes_set(gid, k, v)
        eccodes.codes_set_values(gid, np.arange(1440 * 721, dtype=float))
        msg = eccodes.codes_get_message(gid)
        pts = [(40.58, -111.64), (43.05, 141.32), (-33.35, -70.25), (61.2, 12.51)]
        want = [int(eccodes.codes_grib_find_nearest(gid, la, lo)[0]["index"]) for la, lo in pts]
    finally:
        eccodes.codes_release(gid)
    values, meta = grib.decode(msg)
    idx = grib.point_index(meta, np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    assert list(idx) == want
    assert np.allclose(grib.at_points(values, idx), want)


def test_local_midnights_follow_the_station_zone():
    pytest.importorskip("tzfpy")
    run = datetime(2026, 2, 1, 0, tzinfo=timezone.utc)
    b = nwp._boundaries([_st("ut", 40.6, -111.6), _st("jp", 43.0, 141.3), _st("no", 61.2, 12.5)],
                        date(2026, 2, 2), date(2026, 2, 2), run)
    assert b.tolist() == [[31.0, 55.0], [15.0, 39.0], [23.0, 47.0]]  # MST, JST, CET


def test_candidate_runs_respect_publish_delay():
    src = sources.SOURCES["ecmwf_ifs"]  # online 7.75 h after the cycle
    as_of = datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)
    assert nwp.candidate_runs(src, as_of, live=False)[0] == datetime(2026, 2, 1, 0, tzinfo=timezone.utc)
    early = as_of - timedelta(minutes=30)
    assert nwp.candidate_runs(src, early, live=False)[0] == datetime(2026, 1, 31, 18, tzinfo=timezone.utc)
    assert nwp.candidate_runs(src, as_of, live=True)[0] == datetime(2026, 2, 1, 0, tzinfo=timezone.utc)
    assert len(nwp.candidate_runs(src, as_of, live=True)) == nwp.MAX_RUNS_BACK


class _Fake(sources.Source):
    """Snow water falls at `rate` mm/h from the run start; runs listed in
    `short` stop at 24 h, runs in `gone` are not on the server."""

    name, label, licence, online_h = "fake", "fake", "", 1.0

    def __init__(self, rate=1.0, short=(), gone=()):
        self.rate, self.short, self.gone, self.calls = rate, set(short), set(gone), []

    def steps(self, run):
        if run in self.gone:
            return None
        return list(range(0, 25 if run in self.short else 97, 6))

    def accum(self, run, steps, lats, lons, elev_m):
        self.calls.append(run)
        return {s: np.full(len(lats), self.rate * s) for s in steps}


def test_daily_from_run_interpolates_to_local_midnight(monkeypatch):
    monkeypatch.setattr(nwp, "station_zone", lambda lat, lon: timezone(timedelta(hours=-7)))
    run = datetime(2026, 2, 1, 0, tzinfo=timezone.utc)
    df = nwp.daily_from_run(_Fake(rate=0.5), run, [_st("a", 40, -111)], date(2026, 2, 1), date(2026, 2, 2))
    # local day = 24 h at 0.5 mm/h = 12 mm water -> 8.4 cm -> 3.307 in, despite 6-hourly steps
    assert df["snowfall_in"].round(3).tolist() == [3.307, 3.307]
    assert df.attrs["run"] == run.isoformat()


def test_daily_snowfall_prefers_a_run_that_reaches_every_day(monkeypatch):
    monkeypatch.setattr(nwp, "station_zone", lambda lat, lon: timezone.utc)
    as_of = datetime(2026, 2, 1, 7, tzinfo=timezone.utc)
    newest, older = datetime(2026, 2, 1, 6, tzinfo=timezone.utc), datetime(2026, 2, 1, 0, tzinfo=timezone.utc)
    fake = _Fake(short={newest})
    monkeypatch.setitem(sources.SOURCES, "fake", fake)
    df = nwp.daily_snowfall([_st("a", 40, 0)], date(2026, 2, 2), date(2026, 2, 4), "fake", as_of, live=False)
    assert df.attrs["run"] == older.isoformat() and df["snowfall_in"].notna().all()
    assert fake.calls == [older]  # the short run was never downloaded

    gone = _Fake(gone={newest, older})
    monkeypatch.setitem(sources.SOURCES, "fake", gone)
    df = nwp.daily_snowfall([_st("a", 40, 0)], date(2026, 2, 2), date(2026, 2, 4), "fake", as_of, live=False)
    assert df.attrs["run"] == datetime(2026, 1, 31, 18, tzinfo=timezone.utc).isoformat()


def test_daily_snowfall_raises_when_nothing_is_online(monkeypatch):
    fake = _Fake()
    fake.steps = lambda run: None
    monkeypatch.setitem(sources.SOURCES, "fake", fake)
    with pytest.raises(LookupError):
        nwp.daily_snowfall([_st("a", 40, 0)], date(2026, 2, 2), date(2026, 2, 4), "fake",
                           datetime(2026, 2, 1, 7, tzinfo=timezone.utc), live=True)


def test_gfs_snow_from_six_hour_buckets():
    # 2 mm of precipitation every 3 h; snowing only in (3, 6] (CSNOW 1).
    # GFS averages CSNOW over 6 h buckets: (0,3] -> 0, (0,6] -> 0.5
    chain, buckets = [3, 6, 9, 12], [0, 0, 6, 6]
    apcp = [np.array([2.0 * k]) for k in (1, 2, 3, 4)]
    csnow = [np.array([0.0]), np.array([0.5]), np.array([0.0]), np.array([0.0])]
    cum = sources.snow_from_buckets(chain, buckets, apcp, csnow)
    assert [float(c[0]) for c in cum] == [0.0, 2.0, 2.0, 2.0]


def test_model_prediction_open_meteo_first_direct_for_the_rest(monkeypatch):
    league = type("L", (), {"name": "x"})()
    feat, net = _st("feat", 40, -111), _st("net", 41, -111)
    monkeypatch.setattr(baselines, "load_stations",
                        lambda name, featured_only=False: [feat] if featured_only else [feat, net])
    got = {}

    def om(lg, day, model, mode, featured_only=False):
        got["om"] = (model, featured_only)
        return pd.DataFrame([{"station_id": "feat", "horizon_h": 24, "snowfall_in": 1.0}])

    def direct(lg, day, source, mode="live", featured_only=False, stations=None):
        got["direct"] = [s.station_id for s in stations]
        return pd.DataFrame([{"station_id": s.station_id, "horizon_h": 24, "snowfall_in": 2.0} for s in stations])

    monkeypatch.setattr(baselines, "openmeteo_prediction", om)
    monkeypatch.setattr(baselines, "direct_prediction", direct)
    out = baselines.model_prediction(league, date(2026, 2, 1), "baseline-ecmwf")
    assert got == {"om": ("ecmwf_ifs025", True), "direct": ["net"]}
    assert dict(zip(out.station_id, out.snowfall_in)) == {"feat": 1.0, "net": 2.0}

    # Open-Meteo out of quota: the direct read covers the featured station too
    def om_down(*a, **k):
        raise RuntimeError("429")

    monkeypatch.setattr(baselines, "openmeteo_prediction", om_down)
    out = baselines.model_prediction(league, date(2026, 2, 1), "baseline-ecmwf")
    assert got["direct"] == ["feat", "net"] and len(out) == 2

    # an Open-Meteo-only model keeps raising, so model_baselines skips it
    with pytest.raises(RuntimeError):
        baselines.model_prediction(league, date(2026, 2, 1), "baseline-icon")

    # PB_NWP_DIRECT=0: Open-Meteo only, as before
    monkeypatch.setenv("PB_NWP_DIRECT", "0")
    monkeypatch.setattr(baselines, "openmeteo_prediction", om)
    monkeypatch.setattr(baselines, "direct_prediction", lambda *a, **k: pytest.fail("direct disabled"))
    assert len(baselines.model_prediction(league, date(2026, 2, 1), "baseline-gfs")) == 1


def test_hindcast_as_of_is_the_baselines_slot():
    class L:
        @staticmethod
        def cutoff_utc(d):
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    assert baselines.nwp_as_of(L, date(2026, 2, 2), "hindcast") == datetime(2026, 2, 1, 21, tzinfo=timezone.utc)
    assert baselines.nwp_as_of(L, date(2020, 1, 1), "live") == datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_terrain_index_follows_open_meteo_rules():
    meta = _meta(50, -125, 40, 40)  # 0.25 deg cells, lat 50..40.25, lon -125..-115.25
    elev, land = np.full(1600, 500.0), np.ones(1600)
    centre, north, south = 4 * 40 + 16, 3 * 40 + 16, 5 * 40 + 16  # (49.0, -121.0) and its neighbours
    elev[south] = 2900.0
    lat, lon = np.array([49.0]), np.array([-121.0])
    # a 3000 m station: the 2900 m cell 28 km away beats the 500 m nearest cell
    assert grib.terrain_index(meta, lat, lon, np.array([3000.0]), elev, land, 0)[0] == centre
    assert grib.terrain_index(meta, lat, lon, np.array([3000.0]), elev, land, 1)[0] == south
    # within 100 m the nearest cell is kept, even if a neighbour matches exactly
    elev[north] = 580.0
    assert grib.terrain_index(meta, lat, lon, np.array([580.0]), elev, land, 1)[0] == centre
    # water cells are never chosen
    land[south] = 0.0
    assert grib.terrain_index(meta, lat, lon, np.array([3000.0]), elev, land, 1)[0] != south

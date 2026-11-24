"""scripts/observers_gate.py (day offset + verdict) and scripts/om_archive.py
(Open-Meteo's daily snowfall from the open-data archive), offline."""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import om_archive  # noqa: E402
import observers_gate as gate  # noqa: E402

WINDOWS = {2024: (date(2023, 12, 1), date(2024, 1, 29)), 2025: (date(2024, 12, 1), date(2025, 1, 29)),
           2026: (date(2025, 12, 1), date(2026, 1, 29))}


def storms(seed=1):
    rng = np.random.default_rng(seed)
    return {s: pd.Series(np.where(rng.random(70) < 0.3, rng.gamma(2.0, 3.0, 70), 0.0),
                         index=[b - timedelta(days=5) + timedelta(days=i) for i in range(70)])
            for s, (b, e) in WINDOWS.items()}


def filed(fc, lag, every=1, mix=None):
    """Reports by filing date: day D's model snow filed under D + lag (a
    morning observer has lag 1); `mix` blends in the next day's snow."""
    out = {}
    for s, f in fc.items():
        for d, v in f.items():
            if (d - date(2000, 1, 1)).days % every == 0:
                nxt = f.get(d + timedelta(days=1), 0.0)
                out[d + timedelta(days=lag)] = v if mix is None else (1 - mix) * v + mix * nxt
    return pd.Series(out).sort_index()


def judge(r, fc, min_days=40):
    model = {s: f[(f.index >= WINDOWS[s][0]) & (f.index <= WINDOWS[s][1])] for s, f in fc.items()}
    return gate.judge(r, model, WINDOWS, min_days=min_days)


def test_morning_and_evening_observers_get_their_day():
    fc = storms()
    morning, evening = judge(filed(fc, 1), fc), judge(filed(fc, 0), fc)
    assert (morning["day_offset"], morning["verdict"]) == (1, "pass") and morning["r_0"] == pytest.approx(1.0)
    assert (evening["day_offset"], evening["verdict"]) == (0, "pass")
    assert morning["r_minus1"] < 0.3 and morning["n_2025"] == 60


def test_misdated_ambiguous_and_sparse_records_are_dropped():
    fc = storms()
    assert judge(filed(fc, 2), fc)["verdict"] == "misdated"  # clear best two days late
    assert judge(filed(fc, 1, mix=0.5), fc)["verdict"] == "ambiguous"  # half of it a day off: no clear day
    sparse = judge(filed(fc, 1, every=3), fc)  # reports one day in three
    assert sparse["verdict"] == "fail" and sparse["n_2024"] < 40
    assert judge(filed({s: f * 0 for s, f in fc.items()}, 1), fc)["verdict"] == "fail"  # never any snow


def test_octahedral_grid_bookkeeping():
    g = om_archive.OctahedralGrid(1280)
    assert g.count == 6_599_680
    for y in (0, 5, 1279, 1280, 2559):
        assert int(g.first(y + 1) if y < 2559 else g.count) - int(g.first(y)) == int(g.nx(y))
    cells, km = g.around(53.95, -121.44)
    assert len(cells) == 9 and km.min() < 10


class _Grid:
    """3 x 3 neighbourhood, cell 4 nearest (the #979 station 1A05P, 1,740 m)."""

    def __init__(self, km):
        self.km = np.array(km)

    def nearest(self, lat, lon):
        return int(np.argmin(self.km))

    def around(self, lat, lon):
        return np.arange(9), self.km


def test_cell_choice_scores_the_nearest_cell_with_its_distance_too(monkeypatch):
    km = [16.3, 9.8, 26.0, 26.6, 7.3, 12.0, 21.2, 7.4, 20.2]
    height = np.array([952, 1143, 1285, 795, 956, 1088, 924, 1006, 987], float)
    monkeypatch.setattr(om_archive, "grid", lambda d: ({"grid": _Grid(km), "Ni": 9}, height, np.ones(9)))
    assert om_archive.cell_for("x", 0, 0, 1740) == 1  # 597 m + 9.8 km x 30 beats the nearest's 784 m + 7.3 km x 30
    assert om_archive.cell_for("x", 0, 0, 1000) == 4  # nearest within 100 m: kept


def test_daily_sums_local_days_with_the_elevation_rule(monkeypatch):
    lat, lon = 49.0, -120.0  # America/Vancouver: UTC-7 on Oct 1 (the API's one offset per request)
    monkeypatch.setattr(om_archive, "zone_name", lambda la, lo: "America/Vancouver")  # tzfpy is optional
    monkeypatch.setattr(om_archive, "cell_for", lambda *a: 0)
    monkeypatch.setattr(om_archive, "grid", lambda d: ({"Ni": 1}, np.array([1000.0]), np.ones(1)))

    def series(domain, variable, cell, start, end):
        idx = pd.date_range(start, end - timedelta(hours=1), freq="h")
        if variable == "temperature_2m":  # -1 C at the cell, -7.5 C at the station 1,000 m higher
            return pd.Series(-1.0, index=idx)
        v = pd.Series(0.0, index=idx)
        v[datetime(2025, 10, 2, 7, tzinfo=timezone.utc)] = 10.0  # hour ending at local midnight: Oct 1
        v[datetime(2025, 10, 2, 8, tzinfo=timezone.utc)] = 5.0  # first hour of Oct 2
        return v

    monkeypatch.setattr(om_archive, "series", series)
    d, info = om_archive.station_daily("ecmwf_ifs", lat, lon, 2000.0, date(2025, 10, 1), date(2025, 10, 3))
    assert info["utc_offset_h"] == -7 and info["rule"] and info["dz_m"] == -1000
    assert d[date(2025, 10, 1)] == pytest.approx(10 * 0.7 / 2.54)
    assert d[date(2025, 10, 2)] == pytest.approx(5 * 0.7 / 2.54) and d[date(2025, 10, 3)] == 0
    assert list(om_archive.below_zero_share([-2, 2, -1], [-2, 2, 1])) == [1.0, 0.0, 0.5]

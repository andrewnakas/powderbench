"""GeoSphere Austria adapter: units, the -1 'no snow' sentinel, per-station
day attribution and per-year caching."""

from datetime import date

import pandas as pd

from powderbench.networks import geosphere


def test_geosphere_daily(tmp_path, monkeypatch):
    calls = []

    def fake_get(params):
        calls.append(params)
        rows = []
        for sid in params["station_ids"].split(","):
            for d, hn in (("2026-01-10", 25.4), ("2026-01-11", -1), ("2026-01-12", None)):
                rows.append({"time": f"{d}T00:00+00:00", "station": int(sid), "shneu_manu": hn,
                             "shneu_manu_flag": 20, "substation": 1})
        return pd.DataFrame(rows)

    monkeypatch.setattr(geosphere, "_get", fake_get)
    monkeypatch.setattr(geosphere, "data_dir", lambda: tmp_path)
    ids = ["119:AT:GEO", "66:AT:GEO"]
    out = geosphere.daily(ids, date(2026, 1, 9), date(2026, 1, 11), {"119:AT:GEO": 0})
    t = out.set_index(["station_id", "date"])
    # offset 0: the report dated D is day D
    assert t.loc[("119:AT:GEO", date(2026, 1, 10)), "snow24"] == 10.0
    assert t.loc[("119:AT:GEO", date(2026, 1, 11)), "snow24"] == 0.0  # -1 = no snow
    # default +1: day D <- the report dated D+1; a blank reading is missing
    assert t.loc[("66:AT:GEO", date(2026, 1, 9)), "snow24"] == 10.0
    assert not t.loc[("66:AT:GEO", date(2026, 1, 11)), "valid"]
    n = len(calls)
    geosphere.daily(ids, date(2026, 1, 9), date(2026, 1, 11))
    assert len(calls) == n  # served from the per-station cache

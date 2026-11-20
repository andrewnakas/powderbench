"""Arpae adapter: JSON-lines parsing, the 06 UTC depth, depth rises to new snow, caching."""

import json
from datetime import date

import pandas as pd

from powderbench.networks import arpae

SID = arpae.station_id("simnpr", 1081750, 4411916)


def _line(when: str, depth_m: float | None, extra: dict | None = None) -> str:
    blocks = [{"vars": {"B01019": {"v": "Lago Scaffaiolo Nivo"}, "B07030": {"v": 1662.0}}}]
    if depth_m is not None:
        blocks.append({"timerange": [254, 0, 0], "level": [1, None, None, None],
                       "vars": {"B13013": {"v": depth_m}}})
    if extra:
        blocks.append({"timerange": [1, 0, 3600], "level": [1, None, None, None], "vars": extra})
    return json.dumps({"version": "0.1", "network": "simnpr", "ident": None, "lon": 1081750, "lat": 4411916,
                       "date": when, "data": blocks})


def test_parse_keeps_snow_depth_only():
    lines = [_line("2026-01-02T06:00:00Z", 0.43), _line("2026-01-02T06:30:00Z", None, {"B13011": {"v": 1.2}})]
    df = arpae.parse(lines)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["sid"] == SID == "simnpr-1081750-4411916:IT:ARPAE"
    assert (r["name"], r["height_m"], r["depth_m"]) == ("Lago Scaffaiolo Nivo", 1662.0, 0.43)
    assert abs(r["lat"] - 44.11916) < 1e-9 and abs(r["lon"] - 10.8175) < 1e-9


def test_morning_depth_is_the_median_of_05_to_07_utc():
    df = arpae.parse([_line("2026-01-02T04:30:00Z", 0.90),  # outside the window
                      _line("2026-01-02T05:00:00Z", 0.40), _line("2026-01-02T06:00:00Z", 0.42),
                      _line("2026-01-02T07:00:00Z", 0.41), _line("2026-01-02T07:30:00Z", 0.90),
                      _line("2026-01-03T06:00:00Z", -0.01)])  # negative: no reading
    deps = arpae.morning_depths(df)
    assert deps[SID] == {date(2026, 1, 2): 41.0}


def test_daily_rise_threshold_and_offset(monkeypatch):
    lines = [_line(f"2026-01-0{d}T06:00:00Z", v) for d, v in ((1, 0.30), (2, 0.31), (3, 0.40), (4, 0.35))]
    monkeypatch.setattr(arpae, "readings", lambda b, e: arpae.parse(lines))
    t = arpae.daily([SID], date(2026, 1, 1), date(2026, 1, 4)).set_index("date")
    assert t.loc[date(2026, 1, 1), "snow24"] == 0.0  # a 1 cm rise is sensor jitter
    assert abs(t.loc[date(2026, 1, 2), "snow24"] - 9 / 2.54) < 1e-3  # 06 UTC D -> D+1
    assert t.loc[date(2026, 1, 3), "snow24"] == 0.0 and t.loc[date(2026, 1, 3), "valid"]  # settling
    assert not t.loc[date(2026, 1, 4), "valid"]  # no reading on the 5th
    t0 = arpae.daily([SID], date(2026, 1, 2), date(2026, 1, 2), {SID: 0}).set_index("date")
    assert abs(t0.loc[date(2026, 1, 2), "snow24"] - 0.0) < 1e-9  # offset 0: the rise 1st -> 2nd (1 cm)


def test_month_cache_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    calls = []

    class R:
        status_code = 200

        def __init__(self, body):
            import gzip
            self.content = gzip.compress(body.encode())

        def raise_for_status(self):
            pass

    def get(url, headers=None, timeout=None):
        calls.append(url)
        return R(_line("2026-01-02T06:00:00Z", 0.43) + "\n")

    monkeypatch.setattr(arpae.requests, "get", get)
    df = arpae.month("2026-01", today=date(2026, 1, 20))
    assert len(df) == 1 and calls[-1].endswith("/storico/2026-01.json.gz")
    import os
    noon = pd.Timestamp("2026-01-20T12:00Z").timestamp()
    os.utime(arpae._cache_path("2026-01"), (noon, noon))
    arpae.month("2026-01", today=date(2026, 1, 20))
    assert len(calls) == 1  # the current month: once per UTC day
    arpae.month("2026-01", today=date(2026, 1, 21))
    assert len(calls) == 2
    os.utime(arpae._cache_path("2026-01"), (noon, noon))
    arpae.month("2026-01", today=date(2026, 4, 2))
    assert len(calls) == 3  # read once more after the month ended
    arpae.month("2026-01", today=date(2026, 5, 2))
    assert len(calls) == 3  # then final

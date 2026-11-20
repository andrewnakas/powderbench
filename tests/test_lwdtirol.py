"""LWD Tirol adapter: SMET parsing, local-midnight depths, depth-rise truth."""

from datetime import date

import pandas as pd

from powderbench.networks import lwdtirol

SMET = """SMET 1.2 ASCII
[HEADER]
station_id = SRET2
nodata = -777
fields = timestamp HS TA
[DATA]
2026-01-09T22:30:00Z 0.500 268.0
2026-01-09T22:50:00Z 0.502 268.0
2026-01-09T23:00:00Z 0.498 268.0
2026-01-09T23:10:00Z 0.500 268.0
2026-01-10T12:00:00Z 0.600 268.0
2026-01-10T22:50:00Z 0.700 268.0
2026-01-10T23:00:00Z 0.704 268.0
2026-01-10T23:20:00Z 0.702 268.0
2026-01-11T23:00:00Z 0.695 268.0
2026-01-12T23:00:00Z -777 268.0
"""


def test_midnight_depths():
    s = lwdtirol.midnight_depths(lwdtirol.parse_smet(SMET))
    # 22:50-23:10 UTC on the 9th is the start of local day the 10th; 22:30 and noon are outside the window
    assert s[date(2026, 1, 10)] == 50.0
    assert s[date(2026, 1, 11)] == 70.2
    assert date(2026, 1, 13) not in s  # nodata


def test_daily_rises(monkeypatch):
    s = lwdtirol.midnight_depths(lwdtirol.parse_smet(SMET))
    monkeypatch.setattr(lwdtirol, "depths", lambda sid, begin: s)
    t = lwdtirol.daily(["SRET2:AT:LWDT"], date(2026, 1, 10), date(2026, 1, 12)).set_index("date")
    assert abs(t.loc[date(2026, 1, 10), "snow24"] - 20.2 / 2.54) < 1e-3
    assert t.loc[date(2026, 1, 11), "snow24"] == 0.0  # settling, not negative snow
    assert not t.loc[date(2026, 1, 12), "valid"]


def test_daily_means_shift():
    df = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-09T23:00:00Z"], utc=True), "hs_cm": [55.0]})
    assert lwdtirol.daily_means(df).to_dict() == {date(2026, 1, 10): 55.0}

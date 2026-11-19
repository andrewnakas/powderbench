"""Meteotrentino adapter: AINEVA HN codes, multi-day readings, morning offset."""

from datetime import date

from powderbench.networks import meteotn


def _obs(d, hn, hnnum, cum=False, hncum="   "):
    return {"DataRilievo": f"{d}T00:00:00", "HN": hn, "HNnum": hnnum, "CheckCum": cum, "HnCum": hncum}


OBS = [
    _obs("2025-01-10", "000", "0  "),
    _obs("2025-01-11", "0", "1  "),  # code "0" = none, though HNnum says 1
    _obs("2025-01-12", "015", "15 ", hncum="///"),
    _obs("2025-01-13", "///", None),
    _obs("2025-01-14", "020", "20 ", hncum="020"),  # covers two days
]


def test_parse_codes():
    df = meteotn.parse_obs(OBS).set_index("date")["hn_cm"]
    assert df[date(2025, 1, 10)] == 0 and df[date(2025, 1, 11)] == 0
    assert df[date(2025, 1, 12)] == 15
    assert df.loc[[date(2025, 1, 13), date(2025, 1, 14)]].isna().all()


def test_offset(monkeypatch):
    rec = meteotn.parse_obs(OBS).set_index("date")["hn_cm"].to_dict()
    monkeypatch.setattr(meteotn, "record", lambda field, b, e: rec)
    t = meteotn.daily({"43BU:IT:METEOTN": "43BU"}, date(2025, 1, 10), date(2025, 1, 13)).set_index("date")
    assert t.loc[date(2025, 1, 10), "snow24"] == 0.0  # read the morning of the 11th
    assert abs(t.loc[date(2025, 1, 11), "snow24"] - 15 / 2.54) < 1e-3  # read the morning of the 12th
    assert not t.loc[date(2025, 1, 12), "valid"]  # /// on the 13th
    assert not t.loc[date(2025, 1, 13), "valid"]  # two-day total on the 14th

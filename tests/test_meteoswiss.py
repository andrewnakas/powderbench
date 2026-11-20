"""MeteoSwiss adapter: CSV parsing, blanks, and the 06-06 UTC dating (offset 0)."""

from datetime import date

from powderbench.networks import meteoswiss

CSV = (
    "station_abbr;reference_timestamp;rre150d0;hns000d0;hto000d0\r\n"
    "PON;15.02.2026 00:00;2.5;4;37\r\n"
    "PON;16.02.2026 00:00;9.7;14;40\r\n"
    "PON;17.02.2026 00:00;4;5;55\r\n"
    "PON;18.02.2026 00:00;0.6;;60\r\n"
).encode("latin1")


def test_parse():
    df = meteoswiss.parse_csv(CSV)
    assert df["date"].tolist() == [date(2026, 2, d) for d in (15, 16, 17, 18)]
    assert df["hn_cm"].tolist()[:3] == [4, 14, 5]
    assert df["hn_cm"].isna().tolist() == [False, False, False, True]
    assert df["hs_cm"].tolist() == [37, 40, 55, 60]


def test_day_d_takes_the_value_dated_d(monkeypatch):
    rec = meteoswiss.parse_csv(CSV).set_index("date")[["hn_cm", "hs_cm"]]
    monkeypatch.setattr(meteoswiss, "record", lambda abbr: rec)
    t = meteoswiss.daily(["PON:CH:MCH"], date(2026, 2, 16), date(2026, 2, 18)).set_index("date")
    # 14 cm fell 06 UTC 16th .. 06 UTC 17th (the depth rose 40 -> 55): day 16
    assert abs(t.loc[date(2026, 2, 16), "snow24"] - 14 / 2.54) < 1e-3
    assert abs(t.loc[date(2026, 2, 17), "snow24"] - 5 / 2.54) < 1e-3
    assert not t.loc[date(2026, 2, 18), "valid"]
    assert t.loc[date(2026, 2, 18), "qc_flag"] == "missing"


def test_url():
    assert meteoswiss._url("PON", "recent").endswith("/pon/ogd-nime_pon_d_recent.csv")

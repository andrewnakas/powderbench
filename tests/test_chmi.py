"""CHMI adapter: CSV/JSON parsing, the snowmaking flag, the morning-reading offset."""

import json
from datetime import date

import pandas as pd

from powderbench.networks import chmi

CSV = (
    "STATION,ELEMENT,TIMEFUNC,DT,VALUE,FLAG,QUALITY,\n"
    "0-20000-0-11643,SNO,06:00,2025-12-29T06:00Z,4.0,,0.0,\n"
    "0-20000-0-11643,SNO,06:00,2025-12-30T06:00Z,0.0,P,0.0,\n"
    "0-20000-0-11643,SNO,06:00,2025-12-31T06:00Z,8.0,A,0.0,\n"
).encode()


def test_parse_csv_drops_snowmaking():
    df = chmi.parse_csv(CSV)
    assert df["date"].tolist() == [date(2025, 12, 29), date(2025, 12, 30)]
    assert df["value"].tolist() == [4.0, 0.0]


def test_parse_json():
    blob = json.dumps({"data": {"data": {"header": "STATION,ELEMENT,VTYPE,DT,VAL,FLAG,QUALITY", "values": [
        ["0-20000-0-11643", "SNO", "06:00", "2026-01-02T06:00:00Z", 19.0, "", 0.0],
        ["0-20000-0-11643", "SCE", "06:00", "2026-01-02T06:00:00Z", 30.0, "", 0.0],
        ["0-20000-0-11643", "SNO", "06:00", "2026-01-03T06:00:00Z", 22.0, "", 4.0]]}}}).encode()
    df = chmi.parse_json(blob)
    assert df[["element", "value"]].values.tolist() == [["SNO", 19.0], ["SCE", 30.0]]  # quality 4 dropped


def test_day_d_takes_the_reading_filed_under_d_by_default_and_d_plus_offset(monkeypatch):
    rec = pd.DataFrame({"SNO": [4.0, 19.0, float("nan")], "SCE": [10.0, 30.0, 50.0]},
                       index=[date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)])
    monkeypatch.setattr(chmi, "record", lambda wsi: rec)
    sid = "0-20000-0-11643:CZ:CHMI"
    t = chmi.daily([sid], date(2026, 1, 1), date(2026, 1, 3)).set_index("date")
    assert abs(t.loc[date(2026, 1, 1), "snow24"] - 4 / 2.54) < 1e-3  # filed under the day it fell
    assert abs(t.loc[date(2026, 1, 2), "snow24"] - 19 / 2.54) < 1e-3
    assert not t.loc[date(2026, 1, 3), "valid"]
    t = chmi.daily([sid], date(2025, 12, 31), date(2026, 1, 2), {sid: 1}).set_index("date")
    assert abs(t.loc[date(2025, 12, 31), "snow24"] - 4 / 2.54) < 1e-3  # offset 1: read the morning of Jan 1
    assert not t.loc[date(2026, 1, 2), "valid"]
    assert chmi.code("0-20000-0-11643:CZ:CHMI") == "0-20000-0-11643"


def test_blank_new_snow_reads_0_when_bare_next_morning(monkeypatch):
    nan = float("nan")
    rec = pd.DataFrame({"SNO": [nan, nan, 4.0, nan], "SCE": [0.0, 0.0, 6.0, 5.0]},
                       index=[date(2026, 11, d) for d in (14, 15, 16, 17)])
    monkeypatch.setattr(chmi, "record", lambda wsi: rec)
    t = chmi.daily(["0-203-0-11420:CZ:CHMI"], date(2026, 11, 14), date(2026, 11, 17)).set_index("date")
    assert t.loc[date(2026, 11, 14), "snow24"] == 0.0  # bare on the 15th
    assert not t.loc[date(2026, 11, 15), "valid"]  # 6 cm lay on the 16th: blank is not 0
    assert abs(t.loc[date(2026, 11, 16), "snow24"] - 4 / 2.54) < 1e-3
    assert not t.loc[date(2026, 11, 17), "valid"]

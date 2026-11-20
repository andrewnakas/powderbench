"""Météo-France daily climatological adapter: parsing, doubtful flags, new-snow
board vs depth-derived truth, gauge drift check."""

import gzip
from datetime import date

from powderbench.networks import meteofrance as mf

SNOW = """NUM_POSTE;NOM_USUEL;LAT;LON;ALTI;AAAAMMJJ;HNEIGEF;QHNEIGEF;NEIGETOT06;QNEIGETOT06
65388406;ST LARY LITA;42.8;0.3;2085;20250110;12;1;80;1
65388406;ST LARY LITA;42.8;0.3;2085;20250111;0;1;88;1
65388406;ST LARY LITA;42.8;0.3;2085;20250112;30;2;88;1
88106001;LE MARKSTEIN;47.9;7.0;1180;20250110;;;20;9
88106001;LE MARKSTEIN;47.9;7.0;1180;20250111;;;35;9
88106001;LE MARKSTEIN;47.9;7.0;1180;20250112;;;50;9
88106001;LE MARKSTEIN;47.9;7.0;1180;20250113;;;48;2
88106001;LE MARKSTEIN;47.9;7.0;1180;20040113;;;99;1
"""
RAIN = """NUM_POSTE;NOM_USUEL;AAAAMMJJ;RR;QRR
88106001;LE MARKSTEIN;20250110;14.0;9
88106001;LE MARKSTEIN;20250111;0.0;9
"""


def _frame():
    gz = lambda s: gzip.compress(s.encode())  # noqa: E731
    return mf.reduce(mf.parse(gz(SNOW), mf.SNOW), mf.parse(gz(RAIN), mf.PRECIP))


def test_parse_reduce():
    df = _frame()
    assert set(df["num_poste"]) == {"65388406", "88106001"}
    assert df["date"].min() == date(2025, 1, 10)  # 2004 row dropped (before 2005)
    lary = df[df.num_poste == "65388406"].set_index("date")
    assert lary.loc[date(2025, 1, 12), "hn_cm"] != lary.loc[date(2025, 1, 12), "hn_cm"]  # doubtful -> NaN
    assert mf.department("65388406") == "65" and mf.kind("65388406:FR:MFHN") == "hn"


def test_daily_board_and_depth(monkeypatch):
    df = _frame()
    monkeypatch.setattr(mf, "record", lambda num: df[df.num_poste == num].set_index("date").sort_index())
    hn = mf.daily(["65388406:FR:MFHN"], date(2025, 1, 10), date(2025, 1, 12)).set_index("date")
    assert abs(hn.loc[date(2025, 1, 10), "snow24"] - 12 / 2.54) < 1e-2  # board dated D = day D
    assert hn.loc[date(2025, 1, 11), "snow24"] == 0.0
    assert not hn.loc[date(2025, 1, 12), "valid"]  # doubtful
    dp = mf.daily(["88106001:FR:MF"], date(2025, 1, 10), date(2025, 1, 12)).set_index("date")
    assert abs(dp.loc[date(2025, 1, 10), "snow24"] - 15 / 2.54) < 1e-2  # 20 -> 35 cm, gauge 14 mm
    assert dp.loc[date(2025, 1, 11), "qc_flag"] == "no_swe_support"  # 35 -> 50 cm, gauge dry = drift
    assert not dp.loc[date(2025, 1, 12), "valid"]  # 13th's depth is doubtful

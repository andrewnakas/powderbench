from datetime import date

import pandas as pd

from powderbench import truth
from powderbench.networks import geosphere_sh


def _fake_year(rows):
    def year(codes, y):
        df = pd.DataFrame(rows, columns=["station", "date", "sh", "rr"])
        return df[df["station"].isin(codes) & df["date"].map(lambda d: d.year == y)]
    return year


def test_gauge_depth_rise_and_drift(monkeypatch):
    rows = [
        ("7", date(2026, 1, 10), -1.0, 4.0),   # bare ground (-1) at 06 UTC on the 10th, 4 mm 10th->11th
        ("7", date(2026, 1, 11), 12.0, -1.0),  # +12 cm on 10->11 with rain; 11->12: no precipitation (-1)
        ("7", date(2026, 1, 12), 25.0, None),  # +13 cm on 11->12 with a dry gauge: drift
        ("7", date(2026, 1, 13), 20.0, None),  # settling: 0 new snow
    ]
    monkeypatch.setattr(geosphere_sh, "_year", _fake_year(rows))
    t = geosphere_sh.daily(["7:AT:GEOSH"], date(2026, 1, 10), date(2026, 1, 13)).set_index("date")
    assert t.loc[date(2026, 1, 10), "snow24"] == round(12 / 2.54, 3)  # the rise 10th -> 11th is day 10's snow
    assert t.loc[date(2026, 1, 11), "qc_flag"] == truth.QC_NO_SWE_SUPPORT
    assert t.loc[date(2026, 1, 12), "snow24"] == 0.0
    assert t.loc[date(2026, 1, 13), "qc_flag"] == truth.QC_MISSING  # no reading on the 14th


def test_gauge_day_offset_zero(monkeypatch):
    rows = [("7", date(2026, 1, 10), 0.0, 1.0), ("7", date(2026, 1, 11), 10.0, 1.0)]
    monkeypatch.setattr(geosphere_sh, "_year", _fake_year(rows))
    t = geosphere_sh.daily(["7:AT:GEOSH"], date(2026, 1, 11), date(2026, 1, 11), {"7:AT:GEOSH": 0})
    assert t.iloc[0]["snow24"] == round(10 / 2.54, 3)  # offset 0: the rise into day D (10th -> 11th)

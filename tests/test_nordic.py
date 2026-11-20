"""SMHI and FMI adapters: morning-to-morning depth deltas, units, sentinels."""

from datetime import date

import pandas as pd

from powderbench.networks import fmi, smhi

SMHI_CSV = """﻿Stationsnamn;Stationsnummer;Stationsnät;Mäthöjd (meter över marken)
Storlien-Storvallen;132180;SMHIs stationsnät;0.0

Datum;Tid (UTC);Snödjup;Kvalitet;;Tidsutsnitt:
2025-01-10;06:00:00;0.50;G;;Kvalitetskontrollerade historiska data
2025-01-11;06:00:00;0.75;G
2025-01-12;06:00:00;0.70;G
2025-01-14;06:00:00;0.80;Y
"""

FMI_XML = "".join(
    f"<wfs:member><BsWfs:BsWfsElement><BsWfs:Time>{t}T00:00:00Z</BsWfs:Time>"
    f"<BsWfs:ParameterName>snow</BsWfs:ParameterName><BsWfs:ParameterValue>{v}</BsWfs:ParameterValue>"
    "</BsWfs:BsWfsElement></wfs:member>"
    for t, v in (("2025-01-10", "-1.0"), ("2025-01-11", "25.4"), ("2025-01-12", "20.0"), ("2025-01-13", "NaN"))
)


def test_smhi_depth_deltas(monkeypatch):
    rec = smhi.parse_csv(SMHI_CSV)
    assert len(rec) == 4
    monkeypatch.setattr(smhi, "depths", lambda code: rec)
    t = smhi.daily(["132180:SE:SMHI"], date(2025, 1, 10), date(2025, 1, 13)).set_index("date")
    assert abs(t.loc[date(2025, 1, 10), "snow24"] - 0.25 * smhi.IN_PER_M) < 1e-2  # +25 cm overnight
    assert t.loc[date(2025, 1, 11), "snow24"] == 0.0  # settling -> 0, not negative
    assert not t.loc[date(2025, 1, 12), "valid"]  # no reading on the 13th
    assert not t.loc[date(2025, 1, 13), "valid"]


def test_fmi_depth_deltas(monkeypatch):
    rec = fmi.parse_wfs(FMI_XML)
    assert rec.loc[0, "depth_cm"] == 0.0  # -1 = no snow cover
    monkeypatch.setattr(fmi, "depths", lambda code, b, e: rec.set_index("date")["depth_cm"])
    t = fmi.daily(["101987:FI:FMI"], date(2025, 1, 10), date(2025, 1, 12)).set_index("date")
    assert t.loc[date(2025, 1, 10), "snow24"] == 10.0  # 0 -> 25.4 cm
    assert t.loc[date(2025, 1, 11), "snow24"] == 0.0
    assert not t.loc[date(2025, 1, 12), "valid"]  # NaN next morning
    assert isinstance(t, pd.DataFrame)


def test_day_offset_zero_uses_the_rise_into_day_d(monkeypatch):
    # day_offset 0: the value dated D is read at the end of day D
    rec = smhi.parse_csv(SMHI_CSV)
    monkeypatch.setattr(smhi, "depths", lambda code: rec)
    t = smhi.daily(["132180:SE:SMHI"], date(2025, 1, 11), date(2025, 1, 11), {"132180:SE:SMHI": 0}).set_index("date")
    assert abs(t.loc[date(2025, 1, 11), "snow24"] - 0.25 * smhi.IN_PER_M) < 1e-2
    frec = fmi.parse_wfs(FMI_XML)
    monkeypatch.setattr(fmi, "depths", lambda code, b, e: frec.set_index("date")["depth_cm"])
    t = fmi.daily(["101987:FI:FMI"], date(2025, 1, 11), date(2025, 1, 11), {"101987:FI:FMI": 0}).set_index("date")
    assert t.loc[date(2025, 1, 11), "snow24"] == 10.0

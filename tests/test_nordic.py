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


FROST_JSON = {"data": [
    {"sourceId": "SN25830:0", "referenceTime": f"2025-01-{d:02d}T00:00:00.000Z",
     "observations": [{"elementId": "surface_snow_thickness", "value": v, "qualityCode": q},
                      {"elementId": "sum(precipitation_amount P1D)", "value": p, "qualityCode": 0}]}
    for d, v, q, p in ((10, 50, 0, 0.0), (11, 70, 2, 8.0), (12, 85, 0, 0.0), (13, 99, 7, 1.0), (14, -1, 0, 0.0))
] + [{"sourceId": "SN25830:1", "referenceTime": "2025-01-10T00:00:00.000Z",
      "observations": [{"elementId": "surface_snow_thickness", "value": 999, "qualityCode": 0}]}]}


def test_frost_depth_deltas_and_drift(monkeypatch):
    from powderbench.networks import frost

    rec = frost.parse(FROST_JSON).set_index("date")
    assert rec.loc[date(2025, 1, 10), "depth_cm"] == 50  # sensor 0 wins over sensor 1
    assert pd.isna(rec.loc[date(2025, 1, 13), "depth_cm"])  # qualityCode 7 = erroneous
    assert rec.loc[date(2025, 1, 14), "depth_cm"] == 0  # -1 = no snow
    monkeypatch.setattr(frost, "record", lambda sn, b, e: rec)
    t = frost.daily(["SN25830:NO:FROST"], date(2025, 1, 10), date(2025, 1, 12)).set_index("date")
    assert abs(t.loc[date(2025, 1, 10), "snow24"] - 20 / 2.54) < 1e-2  # +20 cm, gauge (dated 11th) 8 mm
    assert t.loc[date(2025, 1, 11), "qc_flag"] == "no_swe_support"  # +15 cm, gauge dated 12th dry = drift
    assert not t.loc[date(2025, 1, 12), "valid"]  # next reading voided
    t0 = frost.daily(["SN25830:NO:FROST"], date(2025, 1, 11), date(2025, 1, 11), {"SN25830:NO:FROST": 0})
    assert abs(t0.loc[0, "snow24"] - 20 / 2.54) < 1e-2  # day_offset 0: rise into day D


def test_frost_needs_client_id(monkeypatch):
    import pytest

    from powderbench.networks import frost

    monkeypatch.delenv("FROST_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError, match="FROST_CLIENT_ID"):
        frost.client_id()

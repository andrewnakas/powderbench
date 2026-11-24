"""ECCC snow-on-the-ground adapter: parsing, morning rise, offsets and QC."""

import math
from datetime import date

from powderbench import truth
from powderbench.networks import eccc_depth


def feat(day, dep, flag=None, pr=None, pr_flag=None):
    return {"LOCAL_DATE": f"{day} 00:00:00", "SNOW_ON_GROUND": dep, "SNOW_ON_GROUND_FLAG": flag,
            "TOTAL_PRECIPITATION": pr, "TOTAL_PRECIPITATION_FLAG": pr_flag}


def test_parse_flags():
    s = eccc_depth.parse([feat("2025-12-24", 17, pr=26.8), feat("2025-12-25", None, "T", pr=0.2, pr_flag="T"),
                          feat("2025-12-26", 40, "E"), feat("2025-12-27", None, pr=None)])
    assert s[date(2025, 12, 24)] == (17.0, 26.8)
    assert s[date(2025, 12, 25)] == (0.0, 0.0)  # trace reads 0
    assert math.isnan(s[date(2025, 12, 26)][0])  # estimated is void
    assert math.isnan(s[date(2025, 12, 27)][0]) and math.isnan(s[date(2025, 12, 27)][1])


def test_morning_rise_offset_and_jitter():
    # Golden, Dec 2025: the storm of the 24th shows in the 25th's depth
    s = eccc_depth.parse([feat("2025-12-23", 14, pr=2.7), feat("2025-12-24", 17, pr=26.8),
                          feat("2025-12-25", 39, pr=5.4), feat("2025-12-26", 40, pr=3.7),
                          feat("2025-12-27", 38, pr=0)])
    rows = {r["date"]: r for r in eccc_depth.daily_from(s, "x", date(2025, 12, 23), date(2025, 12, 26), k=1)}
    assert rows[date(2025, 12, 24)]["snow24"] == round(22 / 2.54, 3)
    assert rows[date(2025, 12, 23)]["snow24"] == round(3 / 2.54, 3)
    assert rows[date(2025, 12, 25)]["snow24"] == 0.0  # a 1 cm rise is sensor jitter
    assert rows[date(2025, 12, 26)]["snow24"] == 0.0 and rows[date(2025, 12, 26)]["valid"]  # settling
    k0 = {r["date"]: r for r in eccc_depth.daily_from(s, "x", date(2025, 12, 25), date(2025, 12, 25), k=0)}
    assert k0[date(2025, 12, 25)]["snow24"] == round(22 / 2.54, 3)  # offset 0: the rise D-1 -> D


def test_qc_missing_and_dry_drift():
    s = eccc_depth.parse([feat("2026-01-01", 30, pr=0.0), feat("2026-01-02", 45, pr=0.0),
                          feat("2026-01-03", None), feat("2026-01-04", 50, pr=None), feat("2026-01-05", 62)])
    rows = {r["date"]: r for r in eccc_depth.daily_from(s, "x", date(2026, 1, 1), date(2026, 1, 4), k=1)}
    assert rows[date(2026, 1, 1)]["qc_flag"] == truth.QC_NO_SWE_SUPPORT  # +15 cm on a dry gauge
    assert rows[date(2026, 1, 2)]["qc_flag"] == truth.QC_MISSING
    assert rows[date(2026, 1, 4)]["valid"]  # no gauge value: no drift check
    assert rows[date(2026, 1, 4)]["snow24"] == round(12 / 2.54, 3)


def test_station_id():
    assert eccc_depth.station_id("3050519", "AB") == "3050519:AB:ECCCSD"

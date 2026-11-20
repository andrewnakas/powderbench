from datetime import date

import pandas as pd

from powderbench import truth
from powderbench.networks import arpapie


def _setup(monkeypatch, hist, live):
    monkeypatch.setattr(arpapie, "registry", lambda: [
        {"id_network": "PIE", "station_code": "262", "codice_istat_comune": "001263", "progr_punto_com": 907}])
    monkeypatch.setattr(arpapie, "history", lambda key: pd.DataFrame(hist, columns=["date", "hs_cm"]))
    monkeypatch.setattr(arpapie, "live", lambda: pd.DataFrame(
        [("262", d, v) for d, v in live], columns=["station_code", "date", "hs_cm"]))


def test_history_and_live_merge_into_depth_rises(monkeypatch):
    _setup(monkeypatch,
           hist=[(date(2026, 1, 10), 50.0), (date(2026, 1, 11), 62.0), (date(2026, 1, 12), None)],
           live=[(date(2026, 1, 12), 60.0), (date(2026, 1, 13), 71.0)])  # live fills the 12th
    t = arpapie.daily(["262:IT:PIE"], date(2026, 1, 10), date(2026, 1, 13)).set_index("date")
    assert t.loc[date(2026, 1, 10), "snow24"] == round(12 / 2.54, 3)  # 08:00 10th -> 08:00 11th
    assert t.loc[date(2026, 1, 11), "snow24"] == 0.0                  # settling
    assert t.loc[date(2026, 1, 12), "snow24"] == round(11 / 2.54, 3)
    assert t.loc[date(2026, 1, 13), "qc_flag"] == truth.QC_MISSING     # no reading on the 14th


def test_history_key_and_negative_depth(monkeypatch):
    assert arpapie.history_key({"codice_istat_comune": "001263", "progr_punto_com": 907}) == "PIE-001263-907"
    _setup(monkeypatch, hist=[(date(2026, 1, 10), -1.0), (date(2026, 1, 11), 5.0)], live=[])
    t = arpapie.daily(["262:IT:PIE"], date(2026, 1, 10), date(2026, 1, 10))
    assert t.iloc[0]["qc_flag"] == truth.QC_MISSING  # a negative depth is not a reading

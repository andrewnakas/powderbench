"""BC ASWS adapter (BC's own snow-pillow feed): parsing, local-midnight day
ends at UTC-8 / UTC-7, units, QC, ids — on a recorded slice of the real feed
(tests/fixtures/bcasws: Yellowhead Lake and Floe Lake, 2024-12-05..10, every
hour, with AWDB's daily values for Yellowhead Lake's MSNT twin)."""

import csv
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from powderbench import truth
from powderbench.networks import bcasws

FIX = Path(__file__).parent / "fixtures" / "bcasws"
ROOT = Path(__file__).resolve().parents[1]
YH, FLOE = "1A01P:BC:ASWS", "2C14P:BC:ASWS"


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """The adapter reading the recorded archive slice (no current-season file)."""
    monkeypatch.setattr(bcasws, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(bcasws, "_fetch_reduced", lambda name: bcasws.reduce_csv(FIX / f"{name}.csv")
                        if name.endswith("_Archive") else pd.DataFrame(dtype="float32"))
    bcasws._current.cache_clear()
    bcasws._archive.cache_clear()
    yield
    bcasws._current.cache_clear()
    bcasws._archive.cache_clear()


def test_reduce_keeps_midnight_hours_and_ids():
    w = bcasws.reduce_csv(FIX / "SD_Archive.csv")
    assert list(w.columns) == ["1A01P", "2C14P"]
    assert sorted(set(w.index.hour)) == [6, 7, 8, 9]
    assert w.loc["2024-12-08 08:00", "1A01P"] == 80


def test_end_of_day_pacific_and_mountain(feed):
    obs = bcasws.fetch_daily([YH, FLOE], date(2024, 12, 5), date(2024, 12, 9)).set_index(["station_id", "date"])
    # Yellowhead Lake, UTC-8: median of 07/08/09 UTC on the next day (79, 80, 79 cm)
    assert obs.loc[(YH, date(2024, 12, 7)), "SNWD"] == pytest.approx(79 / 2.54)
    assert obs.loc[(YH, date(2024, 12, 6)), "WTEQ"] == pytest.approx(142 / 25.4)  # 141, 142, 142 mm
    # Floe Lake, UTC-7: 06/07/08 UTC (61, 60, 64 cm) — at UTC-8 it would be 64
    assert bcasws.utc_offset("2C14P") == -7 and bcasws.utc_offset("1A01P") == -8
    assert obs.loc[(FLOE, date(2024, 12, 6)), "SNWD"] == pytest.approx(61 / 2.54)
    assert obs.loc[(FLOE, date(2024, 12, 7)), "WTEQ"] == pytest.approx(200 / 25.4)


def test_daily_storm(feed):
    d = bcasws.daily([YH, FLOE], date(2024, 12, 6), date(2024, 12, 10)).set_index(["station_id", "date"])
    assert d.loc[(YH, date(2024, 12, 6)), "snow24"] == pytest.approx(7 / 2.54)  # 61 -> 68 cm
    assert d.loc[(YH, date(2024, 12, 7)), "snow24"] == pytest.approx(11 / 2.54)
    assert d.loc[(YH, date(2024, 12, 8)), "snow24"] == 0.0  # settling
    big = d.loc[(FLOE, date(2024, 12, 7))]  # +21 cm (8.3"): a big jump, backed by +30 mm SWE
    assert big["valid"] and big["snow24"] == pytest.approx(21 / 2.54)
    assert big["swe24"] == pytest.approx(30 / 25.4)
    assert d.loc[(YH, date(2024, 12, 9)), "valid"]
    assert d.loc[(YH, date(2024, 12, 10)), "qc_flag"] == truth.QC_MISSING  # the slice ends on the 10th


def test_awdb_msnt_day_is_the_next_morning_reading():
    """AWDB's daily MSNT value dated d is BC's 16:00 UTC (08:00 PST) reading on
    d+1 (all 40 overlap stations, 99.99% of station-days) — not local midnight,
    which is why the direct feed doesn't copy AWDB's day."""
    sd = pd.read_csv(FIX / "SD_Archive.csv", index_col=0, parse_dates=[0])["1A01P Yellowhead Lake"]
    for r in csv.DictReader((FIX / "awdb_1A01P.csv").open()):
        morning = sd[pd.Timestamp(r["date"]) + pd.Timedelta(hours=40)]
        assert round(morning / 2.54) == float(r["SNWD"])


def test_qc_density_spikes_and_glitches(tmp_path, monkeypatch):
    days = pd.date_range("2025-01-01", periods=5, freq="D")
    stamps = [d + pd.Timedelta(hours=h) for d in days for h in (31, 32, 33)]  # 07/08/09 UTC next day

    def wide(vals):
        return pd.DataFrame({"9Z99P": [v for v in vals for _ in range(3)]}, index=pd.DatetimeIndex(stamps),
                            dtype="float32")

    sd = wide([100, 100, 130, 131, 132])  # cm: +30 cm (11.8") on the 3rd
    sw = wide([300, 300, 301, 99981, 302])  # mm: SWE barely moves; a 99,981 mm glitch on the 4th
    monkeypatch.setattr(bcasws, "hourly", lambda el, b, e: sd if el == "SNWD" else sw)
    sid = bcasws.station_id("9Z99P")
    d = bcasws.daily([sid], date(2025, 1, 2), date(2025, 1, 6)).set_index("date")
    assert d.loc[date(2025, 1, 2), "snow24"] == 0.0
    assert d.loc[date(2025, 1, 3), "qc_flag"] == truth.QC_NO_SWE_SUPPORT
    assert d.loc[date(2025, 1, 4), "valid"]  # the glitch voids SWE, not the 1 cm depth rise
    assert math.isnan(d.loc[date(2025, 1, 4), "swe24"])
    assert d.loc[date(2025, 1, 6), "qc_flag"] == truth.QC_MISSING  # no reading ends day 6

    thin = wide([2, 2, 2, 2, 2])  # 2 cm of snow holding 30 mm of water: impossible
    monkeypatch.setattr(bcasws, "hourly", lambda el, b, e: thin if el == "SNWD" else wide([30] * 5))
    obs = bcasws.fetch_daily([sid], date(2025, 1, 1), date(2025, 1, 4))
    assert obs["SNWD"].isna().all()


def test_station_list():
    msnt = {r["station_id"].split(":")[0] for r in csv.DictReader((ROOT / "data/stations/bc_asws.csv").open())}
    rows = list(csv.DictReader((ROOT / "data/stations/bc_asws_direct.csv").open()))
    assert rows
    for r in rows:
        bc = r["external_id"]
        assert r["station_id"] == bcasws.station_id(bc) == f"{bc}:BC:ASWS"
        assert bc not in msnt  # those keep their AWDB (MSNT) truth
        assert r["truth_source"] == "bcasws" and r["league"] == "stations"
    assert bcasws.station_id("4A30P") != "4A30P:BC:MSNT"

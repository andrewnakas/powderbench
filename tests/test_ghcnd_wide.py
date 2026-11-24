"""Admission routes of scripts/networks/ghcnd_wide.py: the original observer
rule (ghcnd.py) is unchanged, and the looser rules stay in snow country."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "networks"))
import ghcnd  # noqa: E402
import ghcnd_wide as gw  # noqa: E402

FAR = float("inf")


def test_original_rule_is_base():
    assert gw.route(39.6, -106.0, 2800, "CO", 2010, FAR, True) == "base"  # high, near a SNOTEL
    assert gw.route(44.2, -72.8, 600, "VT", 2010, FAR, False) == "base"  # eastern 500 m rule
    assert gw.route(41.6, -93.6, 250, "IA", 2010, 10, False) == "base"  # 15 km from a ski area, anywhere


def test_wide_routes():
    assert gw.route(40.6, -111.5, 1300, "UT", 2010, FAR, True) == "wide"  # 1,200-1,500 m near a SNOTEL
    assert gw.route(61.2, -149.8, 40, "AK", 2010, FAR, True) == "wide"  # Alaska at any elevation near a SNOTEL
    assert gw.route(44.2, -72.8, 200, "VT", 2010, 20, False) == "wide"  # 15-25 km from a ski area, east of 93W
    assert gw.route(47.4, -121.4, 900, "WA", 2010, 20, True) == "wide"  # ... or near a SNOTEL in the west


def test_wide_routes_stay_out_of_the_plains_and_low_snow_rules():
    assert gw.route(39.7, -104.9, 1650, "CO", 2010, 20, False) is None  # Front Range plains: no SNOTEL within 30 km
    assert gw.route(41.6, -93.6, 250, "IA", 2010, 20, False) is None  # west of 93W: plains
    assert gw.route(40.6, -111.5, 1100, "UT", 2010, FAR, True) is None  # below 1,200 m and no ski area
    assert gw.route(44.2, -72.8, 200, "VT", 2010, 30, False) is None  # ski area beyond 25 km
    assert gw.route(44.2, -72.8, 600, "VT", gw.WIDE_MAX_FIRST_YEAR + 1, FAR, False) is None  # record too short
    assert gw.WIDE_NEAR_SKI_KM > ghcnd.NEAR_SKI_KM and gw.WIDE_WEST_MIN_M < ghcnd.WEST_MIN_M


def test_climatology_pass_rule():
    assert gw.climatology_verdict("wide", None) is None  # not built yet: kept until it is
    assert gw.climatology_verdict("wide", (ghcnd.MIN_WINTER_DOYS - 1, 120.0)) == "thin climatology"
    assert gw.climatology_verdict("rejudge", (ghcnd.MIN_WINTER_DOYS - 1, 120.0)) == "thin climatology"
    assert gw.climatology_verdict("wide", (90, gw.MIN_SEASON_IN - 0.1)) == "< 40 in a season"
    assert gw.climatology_verdict("rejudge", (90, 20.0)) is None  # the floor is for the wide route only
    assert gw.climatology_verdict("wide", (90, gw.MIN_SEASON_IN)) is None


def test_climatology_pass_records_and_prunes(tmp_path, monkeypatch):
    """Dropped stations lose their climatology rows, other rows stay as
    written, and a re-run drops them again from the record alone."""
    import csv

    import pandas as pd

    def rows(sid, doys, mean):
        return [{"station_id": sid, "doy": d, "h24_n": 40, "h24_mean": mean if d in doys else None}
                for d in range(1, 366)]

    climo = tmp_path / "observers.csv.gz"
    pd.DataFrame(rows("OLD", range(1, 366), 0.123) + rows("A", range(1, 366), 0.3)  # solid, ~73"
                 + rows("B", range(100, 111), 0.3) + rows("C", range(1, 366), 0.1)  # thin; ~24"
                 ).to_csv(climo, index=False)
    with (tmp_path / "gate.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station_id", "verdict", "day_offset"])
        w.writerows([[s, "pass", 1] for s in "ABC"])
    cands = [{"station_id": s, "route": "wide", "state": "VT", "name": s} for s in "ABC"]
    monkeypatch.setattr(gw, "candidates", lambda: [dict(c) for c in cands])
    monkeypatch.setattr(gw, "GATE", tmp_path / "gate.csv")
    monkeypatch.setattr(gw, "RECORD", tmp_path / "record.csv")
    monkeypatch.setattr(gw, "OUT", tmp_path / "wide.csv")
    monkeypatch.setattr(ghcnd, "CLIMO", climo)
    monkeypatch.setattr(sys, "argv", ["ghcnd_wide.py"])
    before = pd.read_csv(climo, dtype=str, keep_default_na=False)

    for _ in range(2):
        gw.main()
        with gw.OUT.open() as f:
            assert [r["station_id"] for r in csv.DictReader(f)] == ["A"]
        after = pd.read_csv(climo, dtype=str, keep_default_na=False)
        assert after.equals(before[before["station_id"].isin({"OLD", "A"})].reset_index(drop=True))
        with gw.RECORD.open() as f:
            record = {r["station_id"]: r["verdict"] for r in csv.DictReader(f)}
        assert record == {"A": "kept", "B": "thin climatology", "C": "< 40 in a season"}

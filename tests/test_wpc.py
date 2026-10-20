"""WPC PWPF baseline: grid lookup + unit/alignment handling on a real GRIB."""

from pathlib import Path

import pytest

from powderbench import wpc
from powderbench.stations import Station

eccodes = pytest.importorskip("eccodes")
FIXTURE = Path(__file__).parent / "fixtures" / "wpc" / "prcntil_24hsnow_90pt_sample.grb"


def _st(sid, lat, lon):
    return Station(station_id=sid, slug=sid, name=sid, resort=sid, region="x", state="UT",
                   elevation_ft=9000, latitude=lat, longitude=lon)


def test_nearest_matches_eccodes_and_drops_off_grid():
    stations = [_st("snowbird", 40.56914, -111.65852), _st("mammoth", 37.63, -119.03),
                _st("alaska", 61.2, -149.7)]
    vals, grid = wpc._read(FIXTURE, stations, None)
    assert set(grid.index) == {"snowbird", "mammoth"}  # Alaska is off the CONUS grid
    with FIXTURE.open("rb") as f:
        gid = eccodes.codes_grib_new_from_file(f)
        for s in stations[:2]:
            (near,) = eccodes.codes_grib_find_nearest(gid, s.latitude, s.longitude)
            assert grid.index[s.station_id] == near["index"]
    assert all(v >= -1 for v in vals.values())  # metres; tiny negatives get clipped later


def test_round_alignment_uses_00z_cycle_of_round_day():
    # h24 must be the 24h window ending 06Z D+1 (f030 from 00Z D)
    assert wpc.WINDOWS[24] == (24, 30) and wpc.WINDOWS[48] == (48, 54)

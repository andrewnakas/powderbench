"""Météo-France nivo adapter: morning reading only (the midday one is already
inside the next morning's 24 h board), metres -> inches, per-station offsets,
and both file layouts (older years comma-, the current one semicolon-separated)."""

from datetime import date

import pytest

from powderbench.networks import nivo

HEAD = "lat{s}lon{s}geo_id_wmo{s}name{s}validity_time{s}ht_neige{s}ssfrai{s}perssfrai\n"
ROWS = [  # station, time, ssfrai m, perssfrai
    ("07904", "2025-01-09T06:30:00Z", 0.254, -99), ("07904", "2025-01-09T12:00:00Z", 0.10, -50),
    ("07904", "2025-01-10T06:30:00Z", 0.0, -99), ("07929", "2025-01-10T09:00:00Z", 0.127, -99),
]


def _write(path, sep):
    lines = [HEAD.format(s=sep)] + [sep.join(["45", "6", st, "x", t, "1", str(ss), str(p)]) + "\n" for st, t, ss, p in ROWS]
    import gzip

    with gzip.open(path, "wt") as f:
        f.writelines(lines)


def test_nivo_daily(tmp_path, monkeypatch):
    monkeypatch.setattr(nivo, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(nivo, "_download", lambda year, dest: _write(dest, "," if year < 2026 else ";"))
    nivo._year.cache_clear()
    ids = ["07904:FR:NIVO", "07929:FR:NIVO"]
    t = nivo.daily(ids, date(2025, 1, 8), date(2025, 1, 10), {"07929:FR:NIVO": 0}).set_index(["station_id", "date"])
    assert t.loc[("07904:FR:NIVO", date(2025, 1, 8)), "snow24"] == pytest.approx(10.0, abs=1e-3)  # +1: the 9th's morning board, not +midday
    assert t.loc[("07904:FR:NIVO", date(2025, 1, 9)), "snow24"] == 0.0
    assert t.loc[("07929:FR:NIVO", date(2025, 1, 10)), "snow24"] == pytest.approx(5.0, abs=1e-3)  # offset 0
    assert not t.loc[("07904:FR:NIVO", date(2025, 1, 10)), "valid"]  # no report on the 11th
    nivo._year.cache_clear()

"""NDFD snow baseline: issuance choice (never after as_of) and window sums."""

from datetime import date, datetime, timedelta, timezone

import pytest

from powderbench import ndfd

UTC = timezone.utc
S = type("S", (), {"station_id": "a", "latitude": 40.0, "longitude": -111.0})


def test_latest_key_never_after_as_of(monkeypatch):
    keys = {date(2026, 10, 22): [(datetime(2026, 10, 22, 20, 16, tzinfo=UTC), "k2016"),
                                 (datetime(2026, 10, 22, 21, 16, tzinfo=UTC), "k2116")],
            date(2026, 10, 23): []}
    monkeypatch.setattr(ndfd, "_keys", lambda d: keys.get(d, []))
    assert ndfd.latest_key(datetime(2026, 10, 22, 21, 0, tzinfo=UTC)) == "k2016"
    assert ndfd.latest_key(datetime(2026, 10, 23, 1, 0, tzinfo=UTC)) == "k2116"  # falls back a day


def test_windows_sum_6h_periods_and_skip_short_range(monkeypatch):
    pytest.importorskip("eccodes")
    d = date(2026, 10, 23)
    start = datetime(2026, 10, 23, 6, tzinfo=UTC)
    # 0.01 m per period through 12Z D+2 (no periods to 06Z D+3)
    periods = {start + timedelta(hours=6 * k): {"a": 0.01} for k in range(1, 11)}
    monkeypatch.setattr(ndfd, "latest_key", lambda as_of: "k")
    monkeypatch.setattr(ndfd, "_download", lambda url, dest: True)
    monkeypatch.setattr(ndfd, "_periods", lambda path, stations: periods)
    out = ndfd.ndfd_prediction([S], d, datetime(2026, 10, 22, 21, tzinfo=UTC)).set_index("horizon_h")["snowfall_in"]
    assert out[24] == pytest.approx(4 * 0.01 * 39.3701, abs=0.01)
    assert out[48] == pytest.approx(8 * 0.01 * 39.3701, abs=0.01)
    assert 72 not in out.index

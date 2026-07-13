from datetime import date

from powderbench.snowhistory import _season_cumulatives, _season_levels

# _read_series scales to inches before these run, so inputs here are inches.
IN = 1 / 2.54  # cm -> in helper for readable fixtures


def test_season_cumulatives_deltas_fill_and_gaps():
    rows = [
        (date(2025, 6, 1), 10.0 * IN),
        (date(2025, 6, 8), 40.0 * IN),   # +30 cm lumped onto Jun 8
        (date(2025, 6, 15), 35.0 * IN),  # settling -> no negative delta
        (date(2025, 6, 22), 60.0 * IN),  # +25 cm
        (date(2025, 8, 1), 90.0 * IN),   # 40-day gap: baseline resets
        (date(2025, 8, 8), 100.0 * IN),  # +10 cm
    ]
    out = _season_cumulatives(rows)
    assert list(out) == ["2025"]
    arr = out["2025"]
    first = (date(2025, 6, 1) - date(2025, 4, 1)).days
    last = (date(2025, 8, 8) - date(2025, 4, 1)).days
    assert arr[first - 1] is None and arr[last + 1] is None
    assert arr[first] == 0.0
    jun22 = (date(2025, 6, 22) - date(2025, 4, 1)).days
    assert arr[jun22] == round(55 * IN, 1)  # 55 cm accumulated by Jun 22
    assert arr[jun22 + 10] == arr[jun22]    # flat between readings
    assert arr[last] == round(65 * IN, 1)   # gap reset: +10 cm only


def test_season_cumulatives_splits_seasons():
    rows = [
        (date(2024, 7, 1), 0.0),
        (date(2024, 7, 8), 50.0 * IN),
        (date(2025, 6, 20), 10.0 * IN),
        (date(2025, 6, 27), 30.0 * IN),
    ]
    out = _season_cumulatives(rows)
    assert sorted(out) == ["2024", "2025"]
    assert max(v for v in out["2024"] if v is not None) == round(50 * IN, 1)
    assert max(v for v in out["2025"] if v is not None) == round(20 * IN, 1)


def test_season_levels_tracks_snowpack_not_deltas():
    # SWE snow pillow: the curve is the level itself (peak snowpack), and it
    # can fall (melt) — unlike the monotone cumulative curve.
    rows = [
        (date(2025, 6, 1), 5.0),
        (date(2025, 8, 1), 30.0),   # peak
        (date(2025, 10, 1), 8.0),   # melting out
    ]
    out = _season_levels(rows)
    arr = out["2025"]
    aug1 = (date(2025, 8, 1) - date(2025, 4, 1)).days
    oct1 = (date(2025, 10, 1) - date(2025, 4, 1)).days
    assert arr[aug1] == 30.0
    assert arr[oct1] == 8.0  # level drops — melt is visible, not accumulated away
    assert max(v for v in arr if v is not None) == 30.0

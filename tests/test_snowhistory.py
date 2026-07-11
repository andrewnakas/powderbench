from datetime import date

from powderbench.snowhistory import _season_cumulatives


def test_season_cumulatives_deltas_fill_and_gaps():
    rows = [
        # 2025 season (starts Apr 1 2025): weekly-ish depth readings, cm
        (date(2025, 6, 1), 10.0),
        (date(2025, 6, 8), 40.0),   # +30 cm lumped onto Jun 8
        (date(2025, 6, 15), 35.0),  # settling -> no negative delta
        (date(2025, 6, 22), 60.0),  # +25 cm
        # 40-day gap: baseline resets, no lumped delta
        (date(2025, 8, 1), 90.0),
        (date(2025, 8, 8), 100.0),  # +10 cm
    ]
    out = _season_cumulatives(rows)
    assert list(out) == ["2025"]
    arr = out["2025"]
    first = (date(2025, 6, 1) - date(2025, 4, 1)).days
    last = (date(2025, 8, 8) - date(2025, 4, 1)).days
    assert arr[first - 1] is None and arr[last + 1] is None
    assert arr[first] == 0.0
    # 55 cm accumulated by Jun 22 = 21.7 in, forward-filled across the gap
    jun22 = (date(2025, 6, 22) - date(2025, 4, 1)).days
    assert arr[jun22] == round(55 / 2.54, 1)
    assert arr[jun22 + 10] == arr[jun22]  # flat between readings
    # gap reset: Aug 1 adds nothing, Aug 8 adds 10 cm
    assert arr[last] == round(65 / 2.54, 1)


def test_season_cumulatives_splits_seasons():
    rows = [
        (date(2024, 7, 1), 0.0),
        (date(2024, 7, 8), 50.0),
        # next austral season: the cross-season delta must not leak
        (date(2025, 6, 20), 10.0),
        (date(2025, 6, 27), 30.0),
    ]
    out = _season_cumulatives(rows)
    assert sorted(out) == ["2024", "2025"]
    assert max(v for v in out["2024"] if v is not None) == round(50 / 2.54, 1)
    assert max(v for v in out["2025"] if v is not None) == round(20 / 2.54, 1)

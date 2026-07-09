from datetime import date, datetime, timezone

from powderbench.leagues import get_league, load_leagues


def test_leagues_load():
    names = [l.name for l in load_leagues()]
    assert names == ["northern", "southern"]
    assert get_league("southern").status == "trial"
    assert get_league("northern").truth_source == "snotel"
    assert get_league("southern").truth_source == "era5"


def test_northern_cutoff_and_maturity():
    lg = get_league("northern")
    d = date(2025, 12, 10)
    assert lg.cutoff_utc(d) == datetime(2025, 12, 10, 0, tzinfo=timezone.utc)
    assert lg.matured_at_utc(d) == datetime(2025, 12, 13, 15, tzinfo=timezone.utc)
    assert not lg.matured(d, now=datetime(2025, 12, 13, 14, 59, tzinfo=timezone.utc))
    assert lg.matured(d, now=datetime(2025, 12, 13, 15, 0, tzinfo=timezone.utc))


def test_southern_cutoff_precedes_all_local_day_starts():
    lg = get_league("southern")
    d = date(2026, 7, 20)
    cutoff = lg.cutoff_utc(d)
    assert cutoff == datetime(2026, 7, 19, 11, tzinfo=timezone.utc)
    # local day-D starts: NZ (UTC+12) 12:00 UTC D-1; AU (UTC+10) 14:00 UTC D-1;
    # Chile winter (UTC-4) 04:00 UTC D. The cutoff must precede all of them.
    nz_start = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)
    au_start = datetime(2026, 7, 19, 14, tzinfo=timezone.utc)
    cl_start = datetime(2026, 7, 20, 4, tzinfo=timezone.utc)
    assert cutoff < nz_start < au_start < cl_start


def test_southern_maturity_waits_for_era5_lag():
    lg = get_league("southern")
    d = date(2026, 7, 20)
    assert lg.matured_at_utc(d) == datetime(2026, 7, 28, 16, tzinfo=timezone.utc)

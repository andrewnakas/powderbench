from datetime import date, datetime, timezone

from powderbench.leagues import get_league, load_leagues


def test_leagues_load():
    names = [l.name for l in load_leagues()]
    assert names == ["stations", "era5", "resorts"]
    assert all(l.status == "live" for l in load_leagues())
    assert get_league("stations").truth_source == "snotel"
    assert get_league("era5").truth_source == "era5"
    assert get_league("resorts").truth_source == "resort"


def test_stations_cutoff_and_maturity():
    lg = get_league("stations")
    d = date(2025, 12, 10)
    assert lg.cutoff_utc(d) == datetime(2025, 12, 10, 0, tzinfo=timezone.utc)
    assert lg.matured_at_utc(d) == datetime(2025, 12, 13, 15, tzinfo=timezone.utc)
    assert not lg.matured(d, now=datetime(2025, 12, 13, 14, 59, tzinfo=timezone.utc))
    assert lg.matured(d, now=datetime(2025, 12, 13, 15, 0, tzinfo=timezone.utc))


def test_southern_cutoffs_precede_all_local_day_starts():
    d = date(2026, 7, 20)
    for name in ("era5", "resorts"):
        cutoff = get_league(name).cutoff_utc(d)
        assert cutoff == datetime(2026, 7, 19, 11, tzinfo=timezone.utc)
        # local day-D starts: NZ (UTC+12) 12:00 UTC D-1; AU (UTC+10) 14:00 UTC D-1;
        # Chile winter (UTC-4) 04:00 UTC D. The cutoff must precede all of them.
        nz_start = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)
        au_start = datetime(2026, 7, 19, 14, tzinfo=timezone.utc)
        cl_start = datetime(2026, 7, 20, 4, tzinfo=timezone.utc)
        assert cutoff < nz_start < au_start < cl_start


def test_era5_maturity_waits_for_archive_lag():
    lg = get_league("era5")
    d = date(2026, 7, 20)
    assert lg.matured_at_utc(d) == datetime(2026, 7, 28, 16, tzinfo=timezone.utc)


def test_resorts_maturity_waits_for_last_morning_report():
    # snow24(D+2), the last day of the 72h window, appears in morning-of-D+3
    # local reports; the last publishers (Chile/Argentina, UTC-4/-3) are
    # archived by the 13:00 UTC scrape on D+3, so D+3 16:00 UTC is safe and
    # rides the existing resolve cron.
    lg = get_league("resorts")
    d = date(2026, 7, 20)
    assert lg.matured_at_utc(d) == datetime(2026, 7, 23, 16, tzinfo=timezone.utc)

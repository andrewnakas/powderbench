from datetime import date

import pandas as pd

from powderbench import obsfeeds
from powderbench.snowy_pdf import _axis_window


def test_axis_window_parses_hyplot_header():
    text = "Snowy Hydro Limited\nPeriod 7 Month 01/05/2026 to 01/12/2026 2026\n"
    assert _axis_window(text) == (date(2026, 5, 1), date(2026, 12, 1))
    assert _axis_window("no dates here") is None


def test_feeds_registry_shape():
    names = [f.name for f in obsfeeds.FEEDS]
    assert names == ["ina", "snowyhydro", "niwa", "dga"]
    assert all(f.league == "southern" for f in obsfeeds.FEEDS)
    # niwa activates only with credentials; dga is hard-disabled
    assert not obsfeeds._dga_enabled()


def test_collect_observations_swallow_feed_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))

    def boom(begin, end):
        raise RuntimeError("upstream down")

    ok = pd.DataFrame(
        [{"station_id": "perisher:AU:ERA5", "date": date(2026, 7, 4), "snow24_obs_in": 10.9}]
    )
    feeds = (
        obsfeeds.Feed("bad", "southern", lambda: True, boom),
        obsfeeds.Feed("good", "southern", lambda: True, lambda b, e: ok.copy()),
    )
    monkeypatch.setattr(obsfeeds, "FEEDS", feeds)
    out = obsfeeds.collect_observations("southern", date(2026, 7, 1), date(2026, 7, 8))
    assert out["feed"].tolist() == ["good"]
    assert (tmp_path / "obs" / "southern").exists()


def test_non_publishable_feed_stays_private(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    licensed = pd.DataFrame(
        [{"station_id": "remarkables:NZ:ERA5", "date": date(2026, 7, 4), "snow24_obs_in": 6.2}]
    )
    feeds = (
        obsfeeds.Feed("niwa-like", "southern", lambda: True, lambda b, e: licensed.copy(), publish_raw=False),
    )
    monkeypatch.setattr(obsfeeds, "FEEDS", feeds)
    out = obsfeeds.collect_observations("southern", date(2026, 7, 1), date(2026, 7, 8))
    # raw licensed values excluded from the publishable frame (and thus round results)
    assert out.empty
    priv = list((tmp_path / "obs" / "southern" / "private").glob("*.csv"))
    assert len(priv) == 1
    # nothing licensed landed in the public obs dir
    assert not list((tmp_path / "obs" / "southern").glob("*.csv"))


def test_niwa_feed_is_marked_non_publishable():
    niwa = next(f for f in obsfeeds.FEEDS if f.name == "niwa")
    assert niwa.publish_raw is False

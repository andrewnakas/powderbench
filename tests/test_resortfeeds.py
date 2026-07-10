from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from powderbench.resortfeeds import REGISTRY, ResortSpec, archive, parse
from powderbench.resortfeeds import http as rhttp

FIXTURES = Path(__file__).parent / "fixtures" / "resorts"

# expected value (in the spec's unit) parsed from each enabled resort's fixture
FIXTURE_VALUES = {
    "mt-hutt": 158.0,
    "coronet-peak": 40.0,
    "remarkables": 33.0,
    "thredbo": 0.0,
    "catedral": 0.0,
}


def _spec(**kw) -> ResortSpec:
    base = dict(
        resort_id="testy",
        station_id="testy:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url="https://example.com/data.json",
        parse=parse.json_path("snow"),
        unit="cm",
        report_hour_local=8,
        enabled=True,
        verified="2026-07-10: test spec",
    )
    base.update(kw)
    return ResortSpec(**base)


def test_registry_invariants():
    ids = [s.resort_id for s in REGISTRY]
    assert len(ids) == len(set(ids))
    for s in REGISTRY:
        assert s.station_id == f"{s.resort_id}:{s.country}:RESORT"
        assert s.unit in ("cm", "in")
        ZoneInfo(s.tz)  # raises on a bad zone
        if s.enabled:
            # no resort is scraped without a recorded robots/ToS review
            assert s.verified, f"{s.resort_id} enabled without verification stamp"
        else:
            assert s.notes, f"{s.resort_id} disabled without an explanation"


def test_registry_has_launch_coverage():
    enabled = [s for s in REGISTRY if s.enabled]
    assert len(enabled) >= 5
    assert len({s.country for s in enabled}) >= 3


def test_parsers_against_fixtures():
    for spec in REGISTRY:
        if not spec.enabled:
            continue
        matches = list(FIXTURES.glob(f"{spec.resort_id}.*"))
        assert len(matches) == 1, f"expected one fixture for {spec.resort_id}"
        value = spec.parse(matches[0].read_text())
        assert value == FIXTURE_VALUES[spec.resort_id], spec.resort_id


def test_attribution_previous_local_day():
    nz = _spec()
    # 20:30 UTC Jul 15 = 08:30 NZST Jul 16, past the 08:00 refresh -> report
    # describes snow through this morning, i.e. local day Jul 15
    assert archive.attributed_date(datetime(2026, 7, 15, 20, 30, tzinfo=timezone.utc), nz) == date(2026, 7, 15)
    # 13:00 UTC Jul 15 = 01:00 NZST Jul 16, before the refresh -> yesterday's
    # report, describing Jul 14
    assert archive.attributed_date(datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc), nz) == date(2026, 7, 14)

    cl = _spec(resort_id="andino", station_id="andino:CL:RESORT", country="CL", tz="America/Santiago")
    # 13:00 UTC = 09:00 CLT (UTC-4 in July), past the 08:00 refresh -> Jul 14
    assert archive.attributed_date(datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc), cl) == date(2026, 7, 14)


def _fake_http(monkeypatch, bodies: dict[str, str]):
    monkeypatch.setattr(rhttp, "robots_allowed", lambda url, ua=None: True)
    monkeypatch.setattr(
        rhttp, "polite_get",
        lambda url, headers=None, timeout=30: SimpleNamespace(text=bodies[url]),
    )


def test_scrape_roundtrip_direct_first_scrape_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    spec = _spec(url="https://a/x.json")
    monkeypatch.setattr(archive, "REGISTRY", (spec,))

    _fake_http(monkeypatch, {"https://a/x.json": '{"snow": 12.0}'})
    archive.scrape_all(now_utc=datetime(2026, 7, 15, 20, 30, tzinfo=timezone.utc))
    # second scrape lands on the same attributed day with a different value
    _fake_http(monkeypatch, {"https://a/x.json": '{"snow": 20.0}'})
    archive.scrape_all(now_utc=datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc))

    out = archive.daily_from_archive(date(2026, 7, 15), date(2026, 7, 15))
    assert out["station_id"].tolist() == ["testy:NZ:RESORT"]
    # first scrape (closest after the morning refresh) wins: 12 cm = 4.72 in
    assert out["snow24_in"].tolist() == [4.72]


def test_cumulative_deltas_and_gaps(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    spec = _spec(url="https://a/x.json", cumulative=True)
    monkeypatch.setattr(archive, "REGISTRY", (spec,))

    for day, total in ((15, 100.0), (16, 112.0), (18, 130.0)):
        _fake_http(monkeypatch, {"https://a/x.json": '{"snow": %s}' % total})
        archive.scrape_all(now_utc=datetime(2026, 7, day, 20, 30, tzinfo=timezone.utc))

    out = archive.daily_from_archive(date(2026, 7, 15), date(2026, 7, 20))
    # Jul 16 = 12 cm delta; Jul 15 has no prior day and Jul 18 sits after a
    # gap, so neither can be attributed -> absent (missing QC voids later)
    assert [(d.isoformat(), v) for d, v in zip(out["date"], out["snow24_in"])] == [("2026-07-16", 4.72)]


def test_robots_disallow_blocks_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    spec = _spec(url="https://a/x.json")
    monkeypatch.setattr(archive, "REGISTRY", (spec,))
    monkeypatch.setattr(rhttp, "robots_allowed", lambda url, ua=None: False)

    def no_fetch(url, headers=None, timeout=30):
        raise AssertionError("fetch attempted despite robots disallow")

    monkeypatch.setattr(rhttp, "polite_get", no_fetch)
    path = archive.scrape_all(now_utc=datetime(2026, 7, 15, 20, 30, tzinfo=timezone.utc))
    row = pd.read_csv(path).iloc[0]
    assert row["status"] == "robots_blocked"


def test_one_bad_site_never_blocks_the_run(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    bad = _spec(resort_id="bad", station_id="bad:NZ:RESORT", url="https://bad/x")
    good = _spec(resort_id="good", station_id="good:NZ:RESORT", url="https://good/x")
    monkeypatch.setattr(archive, "REGISTRY", (bad, good))
    _fake_http(monkeypatch, {"https://good/x": '{"snow": 5.0}'})  # bad URL missing -> KeyError

    path = archive.scrape_all(now_utc=datetime(2026, 7, 15, 20, 30, tzinfo=timezone.utc))
    df = pd.read_csv(path).set_index("resort_id")
    assert df.loc["bad", "status"] == "http_error"
    assert df.loc["good", "status"] == "ok"


def test_resort_truth_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    import shutil

    src_data = Path(__file__).parent.parent / "data"
    (tmp_path / "resortreports").mkdir()
    shutil.copy(src_data / "leagues.yaml", tmp_path / "leagues.yaml")
    shutil.copy(src_data / "stations.yaml", tmp_path / "stations.yaml")

    raw = tmp_path / "resortreports" / "raw"
    raw.mkdir()
    (raw / "2026-07-16T2030Z.csv").write_text(
        "station_id,resort_id,scraped_utc,attributed_date,value_raw,unit,value_in,kind,status\n"
        "thredbo:AU:RESORT,thredbo,2026-07-16T20:30:00+00:00,2026-07-16,12.0,cm,4.72,snow24,ok\n"
        "mt-hutt:NZ:RESORT,mt-hutt,2026-07-16T20:30:00+00:00,2026-07-16,999.0,cm,393.31,snow24,ok\n"
    )

    # cache-bust the station registry (lru_cache) for the tmp data dir
    from powderbench import stations as stations_mod

    stations_mod._all_stations.cache_clear()
    try:
        from powderbench.leagues import get_league
        from powderbench.truth_sources import daily_truth

        league = get_league("resorts")
        ids = ["thredbo:AU:RESORT", "mt-hutt:NZ:RESORT", "catedral:AR:RESORT"]
        out = daily_truth(league, ids, date(2026, 7, 16), date(2026, 7, 16)).set_index("station_id")
        assert out.loc["thredbo:AU:RESORT", "qc_flag"] == "ok"
        assert out.loc["thredbo:AU:RESORT", "snow24"] == 4.72
        # a 393" day is beyond MAX_PLAUSIBLE_SNOW24_IN -> voided
        assert out.loc["mt-hutt:NZ:RESORT", "qc_flag"] == "implausible_jump"
        assert not out.loc["mt-hutt:NZ:RESORT", "valid"]
        # never scraped -> missing
        assert out.loc["catedral:AR:RESORT", "qc_flag"] == "missing"
        assert not out.loc["catedral:AR:RESORT", "valid"]
    finally:
        stations_mod._all_stations.cache_clear()

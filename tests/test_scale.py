"""Full-network scale: generated station files, featured-set coverage,
side tracks, round snapshots, league scheduling."""

import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from powderbench import HORIZONS, scoring
from powderbench import stations as stations_mod
from powderbench.leagues import load_leagues
from powderbench.stations import Station, load_stations, station_ids
from powderbench.validate import validate_submission

DATA = Path(__file__).resolve().parents[1] / "data"


def _st(sid, region, tier=None):
    return Station(station_id=sid, slug=sid, name=sid, resort=sid, region=region, state="UT",
                   elevation_ft=9000, latitude=40.0, longitude=-111.0, tier=tier)


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """A data dir with the real YAML registries plus a 3-station network file."""
    for name in ("stations.yaml", "leagues.yaml"):
        shutil.copy(DATA / name, tmp_path / name)
    (tmp_path / "stations").mkdir()
    (tmp_path / "stations" / "testnet.csv").write_text(
        "station_id,slug,name,resort,region,state,elevation_ft,latitude,longitude,league,network\n"
        "1:UT:TEST,t1,One,One,Uintas,UT,9000,40.7,-110.9,stations,TEST\n"
        "2:UT:TEST,t2,Two,Two,Uintas,UT,9100,40.71,-110.91,stations,TEST\n"
        # duplicate of a curated station: the curated YAML entry wins
        "766:UT:SNTL,dup,Dup,Dup,Nowhere,UT,1,0,0,stations,TEST\n"
    )
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    stations_mod.clear_cache()
    yield tmp_path
    monkeypatch.delenv("POWDERBENCH_DATA_DIR")
    stations_mod.clear_cache()


def test_network_file_merges_under_curated(tmp_data):
    sts = {s.station_id: s for s in load_stations("stations")}
    assert sts["1:UT:TEST"].tier == "network" and not sts["1:UT:TEST"].featured
    assert sts["766:UT:SNTL"].featured and sts["766:UT:SNTL"].slug == "snowbird"
    assert "1:UT:TEST" not in station_ids("stations", featured_only=True)


def test_unknown_station_field_rejected(tmp_data):
    (tmp_data / "stations" / "bad.csv").write_text(
        "station_id,slug,name,resort,region,state,elevation_ft,latitude,longitude,bogus\n"
        "x,x,x,x,x,UT,1,0,0,1\n"
    )
    stations_mod.clear_cache()
    with pytest.raises(ValueError, match="bogus"):
        load_stations("stations")


def test_coverage_counts_featured_only(tmp_data, tmp_path):
    featured = station_ids("stations", featured_only=True)
    rows = [{"station_id": s, "horizon_h": h, "snowfall_in": 1.0} for s in featured for h in HORIZONS]
    p = tmp_path / "sub.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    res = validate_submission(p, league="stations")
    assert res.ok and res.coverage == 1.0  # network stations are optional


def test_round_snapshot_governs_validation(tmp_data, tmp_path):
    p = tmp_path / "sub.csv"
    pd.DataFrame([{"station_id": "1:UT:TEST", "horizon_h": 24, "snowfall_in": 1.0}]).to_csv(p, index=False)
    assert validate_submission(p, league="stations").ok
    # a round opened before the station existed doesn't know it
    assert not validate_submission(p, league="stations", round_stations=["766:UT:SNTL"]).ok


def test_unique_station_ids_and_slugs():
    """Real registry: ids unique overall, slugs unique per league (site files)."""
    stations_mod.clear_cache()
    all_ids = [s.station_id for lg in load_leagues() for s in load_stations(lg.name)]
    assert len(all_ids) == len(set(all_ids))
    for lg in load_leagues():
        slugs = [s.slug for s in load_stations(lg.name)]
        assert len(slugs) == len(set(slugs)), lg.name


def _truth(ids):
    return pd.DataFrame([{"station_id": s, "horizon_h": h, "truth_in": 2.0 * (h // 24), "valid": True}
                         for s in ids for h in HORIZONS])


def test_score_tracks_headline_is_featured():
    stations = [_st("a", "Wasatch"), _st("b", "Wasatch"), _st("c", "Wasatch"),
                _st("n1", "Uintas", "network"), _st("n2", "Uintas", "network"), _st("n3", "Uintas", "network")]
    ids = [s.station_id for s in stations]
    truth = _truth(ids)
    # perfect on featured, terrible on the network
    pred = truth.rename(columns={"truth_in": "snowfall_in"}).drop(columns="valid")
    pred.loc[pred["station_id"].str.startswith("n"), "snowfall_in"] += 10
    climo = pred.assign(snowfall_in=0.0)
    m = scoring.score_tracks(pred, truth, climo, stations)
    assert m["mae"] == 0 and m["n_scored"] == 9
    assert m["tracks"]["full"]["mae"] == 5.0
    assert m["tracks"]["region:Uintas"]["mae"] == 10.0
    assert m["tracks"]["region:Wasatch"]["powder_score"] == 100.0


def test_powder_score_survives_missing_climatology():
    truth = _truth(["a", "b"])
    pred = truth.rename(columns={"truth_in": "snowfall_in"}).drop(columns="valid")
    climo = pred[pred["station_id"] == "a"].assign(snowfall_in=0.0)  # b has no climatology yet
    m = scoring.score_round(pred, truth, climo)
    assert m["powder_score"] == 100.0 and m["n_skill"] == 3


def test_leagues_due_by_cutoff_hour():
    from typer.testing import CliRunner

    from powderbench import cli
    from powderbench.cli import app

    run = lambda *a: CliRunner().invoke(app, ["leagues", *a]).stdout.split()
    by_cutoff = {lg.name: lg.cutoff_hour_utc for lg in load_leagues()}
    for lg, h in by_cutoff.items():
        assert lg in run("--due", "open", "--hour", str(h))
        assert lg in run("--due", "baselines", "--hour", str((h - cli.BASELINE_LEAD_H) % 24))
        assert lg not in run("--due", "open", "--hour", str((h + 5) % 24))
    assert sorted(run()) == sorted(by_cutoff)


def test_slot_round_is_pinned_to_the_scheduled_slot(monkeypatch):
    """A late baselines run (slot 23:00, runner starts 01:49 next day) must
    still target the round whose cutoff follows the slot — for every league."""
    from datetime import datetime, timezone

    from powderbench import cli
    from powderbench.leagues import get_league

    class Late(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 1, 49, tzinfo=timezone.utc)

    monkeypatch.setattr(cli, "datetime", Late)
    assert cli._slot_round(get_league("stations"), 23) == date(2026, 10, 20)
    assert cli._slot_round(get_league("observers"), 23) == date(2026, 10, 20)

    class OnTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 20, 10, 20, tzinfo=timezone.utc)

    monkeypatch.setattr(cli, "datetime", OnTime)
    assert cli._slot_round(get_league("era5"), 10) == date(2026, 7, 21)  # cutoff 11:00 on D-1

from datetime import date, datetime, timezone

from powderbench.leagues import get_league
from powderbench.rounds import missing_rounds, round_dir


def _now(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_missed_alps_round_is_found_while_its_cutoff_is_ahead(tmp_path, monkeypatch):
    alps = get_league("alps")  # round D locks at 21:00 UTC on D-1
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))  # no rounds on disk
    # 2026-11-24 09:00: the 11-25 round (cutoff 11-24 21:00) was never opened;
    # 11-26 (cutoff 11-25 21:00, 36 h away) is not due yet
    assert missing_rounds(alps, _now(2026, 11, 24, 9)) == [date(2026, 11, 25)]
    # after the cutoff it is too late: nothing to open
    assert missing_rounds(alps, _now(2026, 11, 24, 21, 30)) == [date(2026, 11, 26)]


def test_existing_rounds_are_never_reopened(tmp_path, monkeypatch):
    st = get_league("stations")  # round D locks at 00:00 UTC on D
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    now = _now(2026, 11, 24, 9)
    assert missing_rounds(st, now) == [date(2026, 11, 25)]
    rdir = round_dir(st, date(2026, 11, 25))
    rdir.mkdir(parents=True)
    (rdir / "round.json").write_text("{}")
    assert missing_rounds(st, now) == []


def test_off_season_leagues_open_nothing(tmp_path, monkeypatch):
    era5 = get_league("era5")  # austral winter only
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    assert missing_rounds(era5, _now(2026, 11, 24, 9)) == []


def test_open_round_leaves_an_open_round_alone(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from typer.testing import CliRunner

    from powderbench.cli import app

    # a data dir with the league config but a round already open (as by a catch-up run)
    src = Path(__file__).resolve().parents[1] / "data"
    shutil.copy(src / "leagues.yaml", tmp_path / "leagues.yaml")
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    rdir = round_dir(get_league("stations"), date(2026, 11, 25))
    rdir.mkdir(parents=True)
    (rdir / "round.json").write_text(json.dumps({"stations": ["frozen"]}))
    out = CliRunner().invoke(app, ["open-round", "-l", "stations", "--round-date", "2026-11-25"])
    assert out.exit_code == 0, out.output
    assert "already open" in out.output
    assert json.loads((rdir / "round.json").read_text()) == {"stations": ["frozen"]}

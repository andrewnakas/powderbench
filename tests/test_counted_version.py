"""A submission is scored as it stood at the cutoff: updates merged later
do not count, and a file that first landed late is flagged."""

import subprocess
from datetime import datetime, timedelta, timezone

from powderbench.rounds import counted_version


def _commit(repo, when, msg):
    env = {"GIT_AUTHOR_DATE": when.isoformat(), "GIT_COMMITTER_DATE": when.isoformat(),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True, env=env)


def test_update_after_the_cutoff_is_not_scored(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sub = tmp_path / "data" / "team.csv"
    sub.parent.mkdir(parents=True)
    cutoff = datetime(2026, 11, 26, 0, 5, tzinfo=timezone.utc)

    sub.write_text("station_id,horizon_h,snowfall_in\nA,24,1.0\n")
    _commit(tmp_path, cutoff - timedelta(hours=10), "first")
    sub.write_text("station_id,horizon_h,snowfall_in\nA,24,2.0\n")
    _commit(tmp_path, cutoff - timedelta(hours=1), "update in time")
    sub.write_text("station_id,horizon_h,snowfall_in\nA,24,9.0\n")
    _commit(tmp_path, cutoff + timedelta(hours=2), "update too late")

    text, late = counted_version(sub, cutoff)
    assert not late
    assert text.endswith("A,24,2.0\n")


def test_file_that_first_landed_late_is_flagged(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sub = tmp_path / "team.csv"
    cutoff = datetime(2026, 11, 26, 0, 5, tzinfo=timezone.utc)
    sub.write_text("station_id,horizon_h,snowfall_in\nA,24,1.0\n")
    _commit(tmp_path, cutoff + timedelta(minutes=1), "late")
    assert counted_version(sub, cutoff) == (None, True)


def test_file_outside_git_is_late(tmp_path):
    sub = tmp_path / "team.csv"
    sub.write_text("station_id,horizon_h,snowfall_in\nA,24,1.0\n")
    assert counted_version(sub, datetime.now(timezone.utc)) == (None, True)

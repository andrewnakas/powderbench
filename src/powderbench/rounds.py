"""Live round lifecycle.

A round is named by its cutoff date D: submissions lock at 00:00 UTC on D,
target windows are station-local days D (24h), D..D+1 (48h), D..D+2 (72h).
Truth for D+2 is final once SNOTEL posts the end-of-day reading, so a round
matures for resolution at 15:00 UTC on D+3.

Anti-cheat: a submission only counts as on-time if its file first landed on
the main branch (merge commit time, which GitHub sets and authors can't forge)
before the cutoff. Late files are still scored but flagged and excluded from
official leaderboard aggregation.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import HORIZONS, baselines, climatology, scoring, snotel, truth
from .stations import data_dir, station_ids
from .validate import validate_submission

log = logging.getLogger(__name__)

GRACE_MINUTES = 5


def round_dir(d: date) -> Path:
    return data_dir() / "rounds" / d.isoformat()


def submissions_dir(d: date) -> Path:
    return data_dir() / "submissions" / d.isoformat()


def cutoff_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def matured(d: date, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now >= cutoff_utc(d) + timedelta(days=3, hours=15)


def open_round(d: date) -> Path:
    rdir = round_dir(d)
    rdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "round_id": d.isoformat(),
        "cutoff_utc": cutoff_utc(d).isoformat(),
        "target_days": [(d + timedelta(days=i)).isoformat() for i in range(3)],
        "horizons_h": list(HORIZONS),
        "stations": station_ids(),
        "status": "open",
        "opened_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = rdir / "round.json"
    path.write_text(json.dumps(manifest, indent=1))
    submissions_dir(d).mkdir(parents=True, exist_ok=True)
    keep = submissions_dir(d) / ".gitkeep"
    keep.touch()
    return path


def submit_baselines(d: date, mode: str = "live") -> dict[str, Path]:
    """Write baseline submissions for round d (run shortly before the cutoff).
    mode="hindcast" pulls archived forecasts instead — used for dry runs."""
    out = {}
    sdir = submissions_dir(d)
    sdir.mkdir(parents=True, exist_ok=True)
    for team, pred in baselines.all_baselines(d, mode=mode).items():
        path = sdir / f"{team}.csv"
        pred.to_csv(path, index=False)
        out[team] = path
    return out


def _first_commit_utc(path: Path) -> datetime | None:
    """When the file first landed on the current branch (committer time)."""
    try:
        out = subprocess.run(
            ["git", "log", "--follow", "--diff-filter=A", "--format=%cI", "--", str(path)],
            capture_output=True, text=True, cwd=path.parent, check=True,
        ).stdout.strip().splitlines()
        return datetime.fromisoformat(out[-1]) if out else None
    except Exception:
        return None


def resolve_round(d: date, enforce_deadline: bool = True) -> dict | None:
    """Score all submissions for round d against QC'd truth. Returns the round
    result payload, or None if the round hasn't matured."""
    if not matured(d):
        return None
    obs = snotel.fetch_daily(station_ids(), d - timedelta(days=1), d + timedelta(days=2))
    daily = truth.daily_snowfall(obs)
    truth_d = truth.window_truth(daily, d)

    rdir = round_dir(d)
    rdir.mkdir(parents=True, exist_ok=True)
    truth_d.to_csv(rdir / "truth.csv", index=False)

    climo_pred = climatology.climatology_prediction(d)
    deadline = cutoff_utc(d) + timedelta(minutes=GRACE_MINUTES)
    teams: dict[str, dict] = {}
    for sub_path in sorted(submissions_dir(d).glob("*.csv")):
        team = sub_path.stem
        res = validate_submission(sub_path)
        if not res.ok:
            teams[team] = {"invalid": True, "errors": res.errors}
            continue
        metrics = scoring.score_round(pd.read_csv(sub_path), truth_d, climo_pred=climo_pred)
        if enforce_deadline and not team.startswith("baseline-"):
            landed = _first_commit_utc(sub_path)
            metrics["late"] = landed is None or landed > deadline
        else:
            metrics["late"] = False
        teams[team] = metrics

    payload = {
        "round_id": d.isoformat(),
        "resolved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "qc": {
            "station_horizons_valid": int(truth_d["valid"].sum()),
            "station_horizons_voided": int((~truth_d["valid"]).sum()),
        },
        "teams": teams,
    }
    results_dir = data_dir() / "results" / "rounds"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{d.isoformat()}.json").write_text(json.dumps(payload, indent=1))

    manifest_path = rdir / "round.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "resolved"
        manifest_path.write_text(json.dumps(manifest, indent=1))
    return payload


def resolve_matured() -> list[str]:
    """Resolve every open round that has matured. Returns resolved round ids."""
    resolved = []
    rounds_root = data_dir() / "rounds"
    if not rounds_root.exists():
        return resolved
    for rdir in sorted(rounds_root.iterdir()):
        manifest_path = rdir / "round.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("status") == "resolved":
            continue
        d = date.fromisoformat(manifest["round_id"])
        if resolve_round(d):
            resolved.append(manifest["round_id"])
    return resolved

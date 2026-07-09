"""Live round lifecycle, per league.

A round is named by its target-start date D. The league config sets when
submissions lock (northern: 00:00 UTC on D; southern: 11:00 UTC on D-1, ahead
of every station's local day-D start) and when the round matures for scoring
(northern: D+3 15:00 UTC once SNOTEL posts end-of-day D+2; southern: D+8,
after ERA5's ~5-day archive lag).

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

from . import HORIZONS, baselines, climatology, scoring, truth
from .leagues import League, get_league, load_leagues
from .stations import by_id, data_dir, station_ids
from .truth_sources import daily_truth
from .validate import validate_submission

log = logging.getLogger(__name__)

GRACE_MINUTES = 5


def round_dir(league: League, d: date) -> Path:
    return data_dir() / "rounds" / league.name / d.isoformat()


def submissions_dir(league: League, d: date) -> Path:
    return data_dir() / "submissions" / league.name / d.isoformat()


def results_dir(league: League) -> Path:
    return data_dir() / "results" / league.name / "rounds"


def open_round(league: League, d: date) -> Path:
    rdir = round_dir(league, d)
    rdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "round_id": d.isoformat(),
        "league": league.name,
        "truth_source": league.truth_source,
        "cutoff_utc": league.cutoff_utc(d).isoformat(),
        "target_days": [(d + timedelta(days=i)).isoformat() for i in range(3)],
        "horizons_h": list(HORIZONS),
        "stations": station_ids(league.name),
        "status": "open",
        "opened_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = rdir / "round.json"
    path.write_text(json.dumps(manifest, indent=1))
    sdir = submissions_dir(league, d)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / ".gitkeep").touch()
    return path


def submit_baselines(league: League, d: date, mode: str = "live") -> dict[str, Path]:
    """Write baseline submissions for round d (run shortly before the cutoff).
    mode="hindcast" pulls archived forecasts instead — used for dry runs."""
    out = {}
    sdir = submissions_dir(league, d)
    sdir.mkdir(parents=True, exist_ok=True)
    for team, pred in baselines.all_baselines(league, d, mode=mode).items():
        path = sdir / f"{team}.csv"
        pred.to_csv(path, index=False)
        out[team] = path
    return out


def _biggest_24h(truth_d: pd.DataFrame, league: League) -> dict | None:
    """The deepest valid 24h total this round — the storm headline."""
    h24 = truth_d[(truth_d["horizon_h"] == 24) & truth_d["valid"]]
    if not len(h24) or h24["truth_in"].max() <= 0:
        return None
    top = h24.loc[h24["truth_in"].idxmax()]
    station = by_id(league.name).get(top["station_id"])
    return {
        "station_id": top["station_id"],
        "resort": station.resort if station else top["station_id"],
        "inches": round(float(top["truth_in"]), 1),
    }


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


def resolve_round(league: League, d: date, enforce_deadline: bool = True) -> dict | None:
    """Score all submissions for round d against QC'd truth. Returns the round
    result payload, or None if the round hasn't matured."""
    if not league.matured(d):
        return None
    daily = daily_truth(league, station_ids(league.name), d - timedelta(days=1), d + timedelta(days=2))
    truth_d = truth.window_truth(daily, d)

    rdir = round_dir(league, d)
    rdir.mkdir(parents=True, exist_ok=True)
    truth_d.to_csv(rdir / "truth.csv", index=False)

    climo_pred = climatology.climatology_prediction(d, league)
    deadline = league.cutoff_utc(d) + timedelta(minutes=GRACE_MINUTES)
    teams: dict[str, dict] = {}
    for sub_path in sorted(submissions_dir(league, d).glob("*.csv")):
        team = sub_path.stem
        res = validate_submission(sub_path, league=league)
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

    obs_ref = None
    try:
        from .obsfeeds import collect_observations

        obs = collect_observations(league.name, d, d + timedelta(days=2))
        if len(obs):
            obs_ref = obs.groupby(["station_id", "feed"])["snow24_obs_in"].sum().reset_index().to_dict("records")
    except Exception:
        log.warning("observation feeds failed for %s/%s", league.name, d, exc_info=True)

    payload = {
        "round_id": d.isoformat(),
        "league": league.name,
        "resolved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "observations_72h": obs_ref,
        "qc": {
            "station_horizons_valid": int(truth_d["valid"].sum()),
            "station_horizons_voided": int((~truth_d["valid"]).sum()),
        },
        "biggest_24h": _biggest_24h(truth_d, league),
        "teams": teams,
    }
    rdir_results = results_dir(league)
    rdir_results.mkdir(parents=True, exist_ok=True)
    (rdir_results / f"{d.isoformat()}.json").write_text(json.dumps(payload, indent=1))

    manifest_path = rdir / "round.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "resolved"
        manifest_path.write_text(json.dumps(manifest, indent=1))
    return payload


def resolve_matured(league: League | None = None) -> list[str]:
    """Resolve every open round that has matured (all leagues by default).
    Returns resolved round ids as '<league>/<date>'."""
    resolved = []
    for lg in [league] if league else load_leagues():
        rounds_root = data_dir() / "rounds" / lg.name
        if not rounds_root.exists():
            continue
        for rdir in sorted(rounds_root.iterdir()):
            manifest_path = rdir / "round.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("status") == "resolved":
                continue
            d = date.fromisoformat(manifest["round_id"])
            if resolve_round(lg, d):
                resolved.append(f"{lg.name}/{manifest['round_id']}")
    return resolved

"""Assemble the static site's data files from benchmark results, per league."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from .leaderboard import leaderboard_path, round_results_dir
from .leagues import load_leagues
from .stations import data_dir, load_stations

RECENT_ROUNDS = 14


def site_dir() -> Path:
    return data_dir().parent / "site"


def build_site() -> list[Path]:
    """Copy per-league leaderboards, registries, and recent rounds into site/data/."""
    out_dir = site_dir() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    leagues = load_leagues()
    leagues_out = out_dir / "leagues.json"
    leagues_out.write_text(
        json.dumps(
            [{"name": l.name, "label": l.label, "status": l.status, "truth_source": l.truth_source} for l in leagues],
            indent=1,
        )
    )
    written.append(leagues_out)

    for league in leagues:
        stations_out = out_dir / f"stations-{league.name}.json"
        stations_out.write_text(json.dumps([asdict(s) for s in load_stations(league.name)], indent=1))
        written.append(stations_out)

        lb = leaderboard_path(league)
        if lb.exists():
            target = out_dir / f"leaderboard-{league.name}.json"
            shutil.copyfile(lb, target)
            written.append(target)

        rounds = sorted(round_results_dir(league).glob("*.json"))[-RECENT_ROUNDS:]
        recent = [json.loads(p.read_text()) for p in rounds]
        recent_out = out_dir / f"recent_rounds-{league.name}.json"
        recent_out.write_text(json.dumps(recent, indent=1))
        written.append(recent_out)
    return written

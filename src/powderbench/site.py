"""Assemble the static site's data files from benchmark results."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from .leaderboard import LEADERBOARD_PATH, ROUND_RESULTS_DIR
from .stations import data_dir, load_stations

RECENT_ROUNDS = 14


def site_dir() -> Path:
    return data_dir().parent / "site"


def build_site() -> list[Path]:
    """Copy leaderboard + registry + recent rounds into site/data/."""
    out_dir = site_dir() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    stations_out = out_dir / "stations.json"
    stations_out.write_text(json.dumps([asdict(s) for s in load_stations()], indent=1))
    written.append(stations_out)

    lb = data_dir() / LEADERBOARD_PATH
    if lb.exists():
        target = out_dir / "leaderboard.json"
        shutil.copyfile(lb, target)
        written.append(target)

    rounds = sorted((data_dir() / ROUND_RESULTS_DIR).glob("*.json"))[-RECENT_ROUNDS:]
    recent = [json.loads(p.read_text()) for p in rounds]
    recent_out = out_dir / "recent_rounds.json"
    recent_out.write_text(json.dumps(recent, indent=1))
    written.append(recent_out)
    return written

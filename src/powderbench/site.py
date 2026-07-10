"""Assemble the static site's data files from benchmark results, per league."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from .leaderboard import leaderboard_path, round_results_dir
from .leagues import League, load_leagues
from .stations import data_dir, load_stations

RECENT_ROUNDS = 14

# first day of each month, day-of-year (non-leap), for climatology bucketing
_MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366]


def site_dir() -> Path:
    return data_dir().parent / "site"


def _doy_month(doy: int) -> int:
    for m in range(12):
        if _MONTH_STARTS[m] <= doy < _MONTH_STARTS[m + 1]:
            return m
    return 11


def _daily_truth_series(league: League) -> dict[str, dict[str, list]]:
    """Per-station daily snowfall from resolved rounds' 24h truth:
    {station_id: {"dates": [...], "in": [...]}}. The resorts league reads its
    scrape archive instead (its truth source), so charts work from day one."""
    series: dict[str, dict[str, list]] = {}
    if league.truth_source == "resort":
        from .resortfeeds import daily_from_archive

        today = date.today()
        obs = daily_from_archive(today - timedelta(days=400), today)
        for r in obs.itertuples():
            s = series.setdefault(r.station_id, {"dates": [], "in": []})
            s["dates"].append(r.date.isoformat())
            s["in"].append(round(float(r.snow24_in), 2))
        return series

    for rdir in sorted((data_dir() / "rounds" / league.name).iterdir()):
        tpath = rdir / "truth.csv"
        if not tpath.exists():
            continue
        with tpath.open() as f:
            for row in csv.DictReader(f):
                if row["horizon_h"] == "24" and row["valid"] == "True":
                    s = series.setdefault(row["station_id"], {"dates": [], "in": []})
                    s["dates"].append(rdir.name)
                    s["in"].append(round(float(row["truth_in"]), 2))
    return series


def _round_history(league: League) -> list[dict]:
    """Slim, complete round archive for the race chart + archive table."""
    out = []
    for p in sorted(round_results_dir(league).glob("*.json")):
        r = json.loads(p.read_text())
        teams = {
            team: round(m["powder_score"], 2)
            for team, m in r.get("teams", {}).items()
            if not m.get("invalid") and not m.get("late") and m.get("powder_score") is not None
        }
        out.append(
            {
                "round_id": r["round_id"],
                "biggest_24h": r.get("biggest_24h"),
                "qc": r.get("qc"),
                "teams": teams,
            }
        )
    return out


def _monthly_climatology(league: League) -> dict[str, list]:
    """{station_id: [12 monthly means of h24 climatological snowfall, inches]}."""
    path = data_dir() / league.climatology_path
    if not path.exists():
        return {}
    sums: dict[str, list[list[float]]] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            v = row.get("h24_mean")
            if not v:
                continue
            m = _doy_month(int(row["doy"]))
            sums.setdefault(row["station_id"], [[0.0, 0] for _ in range(12)])
            sums[row["station_id"]][m][0] += float(v)
            sums[row["station_id"]][m][1] += 1
    return {
        sid: [round(total / n, 3) if n else None for total, n in months]
        for sid, months in sums.items()
    }


def _resort_vs_era5(leagues: dict[str, League]) -> dict[str, dict]:
    """Per resort: the resort-claimed daily series next to ERA5 truth at the
    same coordinates — the public receipts behind the resorts league."""
    if "resorts" not in leagues or "era5" not in leagues:
        return {}
    resort_series = _daily_truth_series(leagues["resorts"])
    era5_series = _daily_truth_series(leagues["era5"])
    out = {}
    for s in load_stations("resorts"):
        era5_id = f"{s.slug}:{s.state}:ERA5"
        out[s.slug] = {
            "name": s.name,
            "resort": resort_series.get(s.station_id, {"dates": [], "in": []}),
            "era5": era5_series.get(era5_id, {"dates": [], "in": []}),
        }
    return out


def build_site() -> list[Path]:
    """Copy per-league leaderboards, registries, rounds, and chart series into
    site/data/."""
    out_dir = site_dir() / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def emit(name: str, payload) -> None:
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=1))
        written.append(path)

    leagues = load_leagues()
    emit(
        "leagues.json",
        [{"name": l.name, "label": l.label, "status": l.status, "truth_source": l.truth_source} for l in leagues],
    )

    for league in leagues:
        emit(f"stations-{league.name}.json", [asdict(s) for s in load_stations(league.name)])

        lb = leaderboard_path(league)
        if lb.exists():
            target = out_dir / f"leaderboard-{league.name}.json"
            shutil.copyfile(lb, target)
            written.append(target)

        history = _round_history(league)
        emit(f"recent_rounds-{league.name}.json", [
            json.loads(p.read_text()) for p in sorted(round_results_dir(league).glob("*.json"))[-RECENT_ROUNDS:]
        ])
        emit(f"history-{league.name}.json", history)
        emit(f"snowfall-{league.name}.json", _daily_truth_series(league))
        emit(f"climo-{league.name}.json", _monthly_climatology(league))

    emit("resort-vs-era5.json", _resort_vs_era5({l.name: l for l in leagues}))
    return written

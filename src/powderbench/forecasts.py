"""Per-station forecast data for the site's map: who predicts what, and how
the last scored round actually turned out.

site/data/forecasts-<league>.json:
  teams:   [{team, kind, score, rank}] ordered best-first by season Powder
           Score (teams with no score yet last), so "the best prediction" for a
           station is simply the first team in this list that forecast it
  current: the newest round with forecasts — round_id, cutoff_utc, status
           (open = still taking forecasts, locked = cutoff passed), target_days,
           rows {station_id: {team: [h24, h48, h72, prob_6in, p10_72, p90_72]}}
  last:    the newest resolved round — round_id, target_days,
           truth {station_id: [h24, h48, h72]}, rows {station_id: {team: [h24, h48, h72]}}

Numbers are cumulative inches from local day D (as submitted), rounded to 0.1.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .leaderboard import leaderboard_path
from .leagues import League
from .stations import data_dir

HIDE = ("baseline-zeros", "baseline-persistence")
# before a league has scores, "best" falls back to this order (the
# maintainers' skill-weighted blends first, then the multi-model consensus)
UNSCORED_ORDER = ("house-gbm", "house-analog", "house-blend", "baseline-consensus", "baseline-nbm",
                  "baseline-wpc", "baseline-ecmwf", "baseline-ndfd")


def _num(v: str | None, nd: int = 1):
    if v in (None, ""):
        return None
    try:
        return round(float(v), nd)
    except ValueError:
        return None


def _read_submission(path: Path) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    with path.open() as f:
        for r in csv.DictReader(f):
            try:
                h = int(float(r["horizon_h"]))
            except (KeyError, ValueError):
                continue
            out.setdefault(r["station_id"], {})[h] = r
    return out


def _teams(league: League) -> list[dict]:
    lb = leaderboard_path(league)
    board = json.loads(lb.read_text()).get("season", []) if lb.exists() else []
    teams = []
    for t in board:
        if t["team"] in HIDE:
            continue
        teams.append({"team": t["team"], "kind": _kind(t["team"]), "score": t.get("powder_score"),
                      "rank": t.get("rank"), "rounds": t.get("rounds")})
    teams.sort(key=lambda t: -(t["score"] if t["score"] is not None else -1e9))
    return teams


def _kind(team: str) -> str:
    return "model" if team.startswith("baseline-") else "house" if team.startswith("house-") else "team"


def _round_dirs(league: League) -> list[Path]:
    root = data_dir() / "rounds" / league.name
    return sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.exists() else []


def _current(league: League) -> dict | None:
    """The newest unresolved round that already has most of the usual
    forecasters in (a round that just opened has only the early birds)."""
    sub_root = data_dir() / "submissions" / league.name
    now = datetime.now(timezone.utc)
    cands = []
    for rdir in _round_dirs(league)[:6]:
        if not (rdir / "round.json").exists() or (rdir / "truth.csv").exists():
            continue
        files = [p for p in sorted((sub_root / rdir.name).glob("*.csv")) if p.stem not in HIDE]
        if files:
            cands.append((rdir, files))
    if not cands:
        return None
    most = max(len(f) for _, f in cands)
    rdir, files = next((c for c in cands if len(c[1]) >= 0.6 * most), cands[0])
    m = json.loads((rdir / "round.json").read_text())
    rows: dict[str, dict[str, list]] = {}
    for p in files:
        for sid, by_h in _read_submission(p).items():
            g = lambda h, c="snowfall_in": _num(by_h.get(h, {}).get(c))  # noqa: E731
            rows.setdefault(sid, {})[p.stem] = [
                g(24), g(48), g(72), _num(by_h.get(24, {}).get("prob_6in"), 2), g(72, "p10"), g(72, "p90")]
    cutoff = datetime.fromisoformat(m["cutoff_utc"])
    return {"round_id": m["round_id"], "cutoff_utc": m["cutoff_utc"],
            "status": "open" if cutoff > now else "locked",
            "target_days": m["target_days"], "rows": rows}


def _last(league: League) -> dict | None:
    sub_root = data_dir() / "submissions" / league.name
    for rdir in _round_dirs(league):
        tpath = rdir / "truth.csv"
        if not tpath.exists():
            continue
        truth: dict[str, list] = {}
        with tpath.open() as f:
            for r in csv.DictReader(f):
                if str(r.get("valid")).lower() not in ("true", "1"):
                    continue
                truth.setdefault(r["station_id"], [None, None, None])[
                    {24: 0, 48: 1, 72: 2}[int(float(r["horizon_h"]))]] = _num(r["truth_in"])
        rows: dict[str, dict[str, list]] = {}
        for p in sorted((sub_root / rdir.name).glob("*.csv")):
            if p.stem in HIDE:
                continue
            for sid, by_h in _read_submission(p).items():
                if sid in truth:
                    rows.setdefault(sid, {})[p.stem] = [_num(by_h.get(h, {}).get("snowfall_in")) for h in (24, 48, 72)]
        if not rows:  # resolved before anyone submitted: look further back
            continue
        m = json.loads((rdir / "round.json").read_text())
        return {"round_id": m["round_id"], "target_days": m["target_days"], "truth": truth, "rows": rows}
    return None


def build_forecasts(league: League) -> dict:
    teams, cur, last = _teams(league), _current(league), _last(league)
    # forecasters with no season score yet still appear, after the scored ones
    seen = {t["team"] for t in teams}
    extra = {team for part in (cur, last) if part for rows in part["rows"].values() for team in rows} - seen
    pref = {t: i for i, t in enumerate(UNSCORED_ORDER)}
    extra = sorted(extra, key=lambda t: (pref.get(t, len(pref)), t))
    teams += [{"team": t, "kind": _kind(t), "score": None, "rank": None, "rounds": 0} for t in extra]
    return {"league": league.name, "generated_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "teams": teams, "current": cur, "last": last}

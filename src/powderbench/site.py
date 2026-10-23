"""Assemble the static site's data files from benchmark results, per league."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .leaderboard import leaderboard_path, round_results_dir
from .leagues import League, load_leagues
from .stations import data_dir, load_stations, station_ids

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
    # per-station daily series are for the featured stations' charts; the
    # full network would be multi-MB of eagerly-loaded JSON
    keep = set(station_ids(league.name, featured_only=True))
    if league.truth_source == "resort":
        from .resortfeeds import daily_from_archive

        today = date.today()
        obs = daily_from_archive(today - timedelta(days=400), today)
        for r in obs.itertuples():
            if r.station_id not in keep:
                continue
            s = series.setdefault(r.station_id, {"dates": [], "in": []})
            s["dates"].append(r.date.isoformat())
            s["in"].append(round(float(r.snow24_in), 2))
        return series

    rounds_root = data_dir() / "rounds" / league.name
    if not rounds_root.exists():  # a league before its first round (e.g. off-season)
        return series
    for rdir in sorted(rounds_root.iterdir()):
        tpath = rdir / "truth.csv"
        if not tpath.exists():
            continue
        with tpath.open() as f:
            for row in csv.DictReader(f):
                if row["horizon_h"] == "24" and row["valid"] == "True" and row["station_id"] in keep:
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
    import pandas as pd

    climo = pd.read_csv(path, usecols=["station_id", "doy", "h24_mean"]).dropna(subset=["h24_mean"])
    # the heatmap is for the featured stations; the full network would be
    # thousands of DOM rows
    climo = climo[climo["station_id"].isin(set(station_ids(league.name, featured_only=True)))]
    sums: dict[str, list[list[float]]] = {}
    for sid, doy, v in zip(climo["station_id"], climo["doy"], climo["h24_mean"]):
        m = _doy_month(int(doy))
        sums.setdefault(sid, [[0.0, 0] for _ in range(12)])
        sums[sid][m][0] += float(v)
        sums[sid][m][1] += 1
    return {
        sid: [round(total / n, 3) if n else None for total, n in months]
        for sid, months in sums.items()
    }


def _league_round_status(league: League) -> tuple[dict | None, str | None]:
    """(newest open round manifest info, last resolved round id) — lets the
    site mark off-season leagues and pick a live default."""
    open_round = None
    rounds_root = data_dir() / "rounds" / league.name
    if rounds_root.exists():
        for rdir in sorted((p for p in rounds_root.iterdir() if p.is_dir()), reverse=True):
            manifest = rdir / "round.json"
            if not manifest.exists():
                continue
            r = json.loads(manifest.read_text())
            if r.get("status") == "open":
                open_round = {"round_id": r["round_id"], "cutoff_utc": r["cutoff_utc"]}
                break
    resolved = sorted(round_results_dir(league).glob("*.json"))
    return open_round, (resolved[-1].stem if resolved else None)


# history spans mirror the climatology builds (TruthSource.history_begin), so
# the big fetch is a cache hit
_HISTORY_SPLIT = date(2025, 6, 30)


def build_history(league: League, seasons: int = 12) -> list[Path]:
    """Export per-station season-cumulative snowfall JSON for the site's
    season explorer: site/data/seasons/<league>/<slug>.json + index.json.
    Run manually (`powderbench build-history`) — history barely changes, and
    the truth fetches ride the climatology cache."""
    import pandas as pd

    from .truth_sources import daily_truth, get_source

    today = date.today()
    # featured stations only: ~1k season files rewritten daily would bloat git
    ids = station_ids(league.name, featured_only=True)
    history_begin = get_source(league.truth_source).history_begin
    if history_begin is None:
        # the scrape archive IS the history; it just starts young
        from .resortfeeds import daily_from_archive

        daily = daily_from_archive(today - timedelta(days=365 * seasons), today)
        daily = daily.rename(columns={"snow24_in": "snow24"})
    else:
        frames = [daily_truth(league, ids, history_begin, _HISTORY_SPLIT)]
        if today > _HISTORY_SPLIT:
            frames.append(daily_truth(league, ids, _HISTORY_SPLIT + timedelta(days=1), today))
        daily = pd.concat(frames, ignore_index=True)
        daily = daily[daily["valid"]]

    ssm = league.season_start_month
    current_season = today.year if today.month >= ssm else today.year - 1
    min_season = current_season - (seasons - 1)

    out_dir = site_dir() / "data" / "seasons" / league.name
    out_dir.mkdir(parents=True, exist_ok=True)
    written, index = [], []
    stations_by_id = {s.station_id: s for s in load_stations(league.name)}
    for sid, grp in daily.groupby("station_id"):
        st = stations_by_id.get(sid)
        if st is None:
            continue
        per_season: dict[str, list] = {}
        last_idx: dict[str, int] = {}
        for d, snow in zip(grp["date"], grp["snow24"]):
            season = d.year if d.month >= ssm else d.year - 1
            if season < min_season:
                continue
            idx = (d - date(season, ssm, 1)).days
            if not (0 <= idx < 365):
                continue
            key = str(season)
            per_season.setdefault(key, [0.0] * 365)[idx] += float(snow)
            last_idx[key] = max(last_idx.get(key, 0), idx)
        cum_seasons = {}
        for key, incs in sorted(per_season.items()):
            total, cum = 0.0, []
            for i, v in enumerate(incs):
                total += v
                cum.append(round(total, 1) if i <= last_idx[key] else None)
            cum_seasons[key] = cum
        payload = {
            "station_id": sid,
            "resort": st.resort,
            "season_start_month": ssm,
            "seasons": cum_seasons,
        }
        path = out_dir / f"{st.slug}.json"
        path.write_text(json.dumps(payload))
        written.append(path)
        latest_total = next(
            (max(v for v in cum_seasons[k] if v is not None) for k in sorted(cum_seasons, reverse=True)), 0
        )
        index.append(
            {"slug": st.slug, "station_id": sid, "resort": st.resort,
             "seasons": sorted(cum_seasons), "latest_total": latest_total}
        )

    index.sort(key=lambda e: -e["latest_total"])
    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps({"season_start_month": ssm, "stations": index}, indent=1))
    written.append(index_path)
    return written


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

    def emit(name: str, payload, compact: bool = False) -> None:
        path = out_dir / name
        path.write_text(json.dumps(payload, separators=(",", ":")) if compact else json.dumps(payload, indent=1))
        written.append(path)

    from .truth_sources import get_source

    def health(l) -> dict:
        """Public data-health line: QC pass rate of the last scored round and,
        for resort leagues, the status mix of the latest scrape."""
        out = {}
        res = sorted(round_results_dir(l).glob("*.json"))
        if res:
            qc = json.loads(res[-1].read_text()).get("qc") or {}
            ok, bad = qc.get("station_horizons_valid", 0), qc.get("station_horizons_voided", 0)
            if ok + bad:
                out["last_round"] = res[-1].stem
                out["qc_valid_share"] = round(ok / (ok + bad), 3)
        if l.truth_source == "resort":
            raw = sorted((data_dir() / "resortreports" / "raw").glob("*.csv"))
            ids = {s.station_id for s in load_stations(l.name)}
            for path in reversed(raw[-6:]):
                with path.open() as f:
                    rows = [r for r in csv.DictReader(f) if r["station_id"] in ids]
                if rows:
                    counts: dict[str, int] = {}
                    for r in rows:
                        counts[r["status"]] = counts.get(r["status"], 0) + 1
                    out["latest_scrape"] = {"at": rows[0]["scraped_utc"], "status": counts}
                    break
        return out

    leagues = load_leagues()
    league_meta = []
    for l in leagues:
        open_round, last_resolved = _league_round_status(l)
        src = get_source(l.truth_source)
        league_meta.append(
            {"name": l.name, "label": l.label, "status": l.status, "truth_source": l.truth_source,
             "truth_note": src.note, "measured": src.measured,
             "n_stations": len(load_stations(l.name)),
             "n_featured": len(load_stations(l.name, featured_only=True)),
             "n_rounds_resolved": len(list(round_results_dir(l).glob("*.json"))),
             "health": health(l),
             "open_round": open_round, "last_resolved": last_resolved}
        )
    emit("leagues.json", league_meta)

    for league in leagues:
        # compact + no empty fields: full-network leagues list thousands of stations
        emit(f"stations-{league.name}.json",
             [{k: v for k, v in asdict(s).items() if v is not None} for s in load_stations(league.name)],
             compact=True)

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
    emit("events.json", _events())
    sources = data_dir() / "sources.yaml"
    if sources.exists():
        import yaml

        emit("sources.json", yaml.safe_load(sources.read_text())["sources"])
    written += build_api(leagues)
    # the methodology and attribution pages render these documents verbatim
    docs = site_dir() / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("METHODOLOGY.md", "SOURCES.md", "RULES.md"):
        src = data_dir().parent / "docs" / name
        if src.exists():
            shutil.copyfile(src, docs / name)
            written.append(docs / name)
    return written


def _events() -> list[dict]:
    """Storm Mode: every event, newest first, with its scoreboard once resolved."""
    root = data_dir() / "events"
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), reverse=True):
        mpath = d / "event.json"
        if not mpath.exists():
            continue
        m = json.loads(mpath.read_text())
        entry = {k: m.get(k) for k in ("event_id", "title", "league", "start", "end", "cutoff_utc", "status")}
        entry["n_stations"] = len(m.get("stations", []))
        res = data_dir() / "results" / "events" / f"{d.name}.json"
        if res.exists():
            r = json.loads(res.read_text())
            board = [{"team": t, "powder_score": v.get("powder_score"), "mae": v.get("mae"), "n": v.get("n_scored")}
                     for t, v in r["teams"].items() if not v.get("invalid") and not v.get("late")]
            entry["board"] = sorted(board, key=lambda b: -(b["powder_score"] if b["powder_score"] is not None else -1e9))
            entry["biggest_total"] = r.get("biggest_total")
        out.append(entry)
    return out


REPO_RAW = "https://raw.githubusercontent.com/andrewnakas/powderbench/main"


def build_api(leagues) -> list[Path]:
    """Agent-facing static API (site/api/v1/): every open round with its
    cutoff, station lists and the exact submission path — so a bot never has
    to scrape the site or walk the repo."""
    from .truth_sources import get_source

    api = site_dir() / "api" / "v1"
    api.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rounds = []
    for lg in leagues:
        root = data_dir() / "rounds" / lg.name
        if not root.exists():
            continue
        for rdir in sorted(p for p in root.iterdir() if p.is_dir()):
            mpath = rdir / "round.json"
            if not mpath.exists():
                continue
            m = json.loads(mpath.read_text())
            # still accepting forecasts (unresolved rounds past cutoff are just waiting on truth)
            if m.get("status") != "open" or datetime.fromisoformat(m["cutoff_utc"]) <= now:
                continue
            rounds.append({
                "league": lg.name,
                "round_id": m["round_id"],
                "cutoff_utc": m["cutoff_utc"],
                "target_days": m.get("target_days"),
                "horizons_h": m.get("horizons_h"),
                "truth_source": lg.truth_source,
                "measured_new_snow": get_source(lg.truth_source).measured,
                "n_stations": len(m.get("stations", [])),
                "n_featured": len(m.get("featured") or m.get("stations", [])),
                "manifest_url": f"{REPO_RAW}/data/rounds/{lg.name}/{m['round_id']}/round.json",
                "stations_url": f"https://powderbench.com/data/stations-{lg.name}.json",
                "submission_path": f"data/submissions/{lg.name}/{m['round_id']}/<your-team>.csv",
            })
    payload = {
        "generated_utc": now.isoformat(timespec="seconds"),
        "note": "refreshed daily ~16 UTC; the round manifest (manifest_url) is canonical",
        "submission_schema": {
            "required": {"station_id": "str", "horizon_h": "24|48|72 (cumulative from round day D)",
                         "snowfall_in": "inches, 0-200"},
            "optional": {"p10,p25,p50,p75,p90": "all five or none, non-decreasing",
                         "prob_6in": "P(>=6in) on horizon-24 rows, 0-1"},
            "coverage_rule": ">=70% of featured station-horizons to be ranked",
            "how": "open a PR adding submission_path before cutoff_utc; a bot validates and auto-merges",
        },
        "open_rounds": rounds,
    }
    path = api / "open-rounds.json"
    path.write_text(json.dumps(payload, indent=1))
    return [path]

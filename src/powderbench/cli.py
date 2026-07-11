"""PowderBench command-line interface."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="PowderBench: live mountain-snowfall forecasting benchmark.", no_args_is_help=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

LeagueOpt = typer.Option("stations", "--league", "-l", help="League: stations | era5 | resorts")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@app.command()
def stations(league: str = LeagueOpt):
    """List a league's station registry."""
    from .stations import load_stations

    for s in load_stations(league):
        typer.echo(f"{s.station_id:>18}  {s.resort:<30} {s.region:<22} {s.elevation_ft:>6.0f} ft")


@app.command()
def validate(submission: Path, league: str = LeagueOpt):
    """Validate a submission CSV (schema, ranges, coverage)."""
    from .validate import validate_submission

    res = validate_submission(submission, league=league)
    typer.echo(res.summary())
    raise typer.Exit(0 if res.ok else 1)


@app.command()
def build_climatology(
    league: str = LeagueOpt,
    begin: Optional[str] = typer.Option(None, help="Default: league-appropriate history"),
    end: Optional[str] = typer.Option(None),
):
    """Rebuild a league's climatology table from its truth history."""
    from .climatology import build_climatology as build
    from .leagues import get_league

    lg = get_league(league)
    defaults = {
        "snotel": ("2015-10-01", "2025-06-30"),
        "era5": ("1991-01-01", "2025-06-30"),
        # resorts league has no report history yet: the reference climatology
        # is ERA5 at the resort coordinates (see docs/DATA.md caveat)
        "resort": ("1991-01-01", "2025-06-30"),
    }[lg.truth_source]
    if lg.truth_source == "resort":
        from dataclasses import replace

        lg = replace(lg, truth_source="era5")
    build(lg, _parse_date(begin or defaults[0]), _parse_date(end or defaults[1]))


@app.command()
def hindcast(
    begin: str,
    end: str,
    league: str = LeagueOpt,
    submission: Optional[Path] = typer.Option(None, help="CSV with round_date column"),
    team: str = typer.Option("you", help="Team name for your submission"),
    out: Optional[Path] = typer.Option(None, help="Write per-round metrics CSV here"),
):
    """Training camp: score baselines (and optionally your forecasts) on past days."""
    from .hindcast import load_hindcast_submission, run_hindcast
    from .leaderboard import aggregate
    from .leagues import get_league

    lg = get_league(league)
    subs = {team: load_hindcast_submission(submission)} if submission else {}
    rows = run_hindcast(lg, _parse_date(begin), _parse_date(end), submissions=subs)
    if not len(rows):
        typer.echo("no scorable rounds in range")
        raise typer.Exit(1)
    if out:
        rows.to_csv(out, index=False)
        typer.echo(f"wrote {out}")
    board = aggregate(rows, min_rounds=1)
    cols = ["rank", "team", "rounds", "powder_score", "mae", "pinball", "brier6", "avg_coverage"]
    typer.echo(f"\n=== {lg.label} hindcast (unofficial — training camp) ===")
    typer.echo(board[cols].to_string(index=False))


@app.command()
def open_round(
    league: str = LeagueOpt,
    round_date: Optional[str] = typer.Option(None, help="Default: next round with a ~24h window"),
):
    """Create the round manifest for a submission day."""
    from .leagues import get_league
    from .rounds import open_round as do_open

    lg = get_league(league)
    # default: the round whose cutoff is ~24h away (cutoff sits days_before ahead of D)
    d = _parse_date(round_date) if round_date else date.today() + timedelta(days=1 + lg.cutoff_days_before)
    path = do_open(lg, d)
    typer.echo(f"opened {lg.name} round {d} -> {path}")


@app.command()
def baseline_submit(
    round_date: str,
    league: str = LeagueOpt,
    mode: str = typer.Option("live", help="live | hindcast (dry runs)"),
):
    """Generate and store baseline submissions for an open round."""
    from .leagues import get_league
    from .rounds import submit_baselines

    for team, path in submit_baselines(get_league(league), _parse_date(round_date), mode=mode).items():
        typer.echo(f"{team}: {path}")


@app.command()
def resolve(
    league: Optional[str] = typer.Option(None, "--league", "-l", help="Default: all leagues"),
    round_date: Optional[str] = typer.Option(None, help="Resolve one round; default: all matured"),
):
    """Fetch truth, score submissions for matured rounds, update results."""
    from .leagues import get_league
    from .rounds import resolve_matured, resolve_round

    if round_date:
        lg = get_league(league or "stations")
        result = resolve_round(lg, _parse_date(round_date))
        typer.echo(json.dumps(result, indent=1, default=str) if result else "not resolvable yet")
    else:
        for rid in resolve_matured(get_league(league) if league else None):
            typer.echo(f"resolved {rid}")


@app.command()
def build_leaderboard(league: Optional[str] = typer.Option(None, "--league", "-l", help="Default: all leagues")):
    """Aggregate resolved rounds into per-league leaderboard.json."""
    from .leaderboard import build_leaderboard as build
    from .leagues import load_leagues

    for lg in load_leagues() if league is None else [next(l for l in load_leagues() if l.name == league)]:
        out = build(lg)
        typer.echo(f"{lg.name}: leaderboard over {out['generated_rounds']} rounds")


@app.command()
def build_history(
    league: str = LeagueOpt,
    seasons: int = typer.Option(12, help="How many past seasons to export"),
):
    """Export per-station season history for the site's season explorer."""
    from .leagues import get_league
    from .site import build_history as build

    files = build(get_league(league), seasons=seasons)
    typer.echo(f"wrote {len(files)} files" if files else "league has no history source")


@app.command()
def scrape_resorts(
    only: Optional[str] = typer.Option(None, help="Scrape a single resort_id"),
    dry_run: bool = typer.Option(False, help="Fetch and parse but write nothing"),
):
    """Scrape enabled resort snow reports into data/resortreports/raw/."""
    from .resortfeeds import REGISTRY, scrape_all

    if dry_run:
        from .resortfeeds.http import polite_get, robots_allowed

        for spec in REGISTRY:
            if not spec.enabled or (only and spec.resort_id != only):
                continue
            if not robots_allowed(spec.url):
                typer.echo(f"{spec.resort_id}: robots_blocked")
                continue
            value = spec.parse(polite_get(spec.url, headers=dict(spec.headers) if spec.headers else None).text)
            typer.echo(f"{spec.resort_id}: {value} {spec.unit}" if value is not None else f"{spec.resort_id}: no report")
        return
    path = scrape_all(only=only)
    typer.echo(f"wrote {path}")


@app.command()
def build_site():
    """Copy leaderboards + station data into site/data for the static site."""
    from .site import build_site as build

    for f in build():
        typer.echo(f"wrote {f}")


if __name__ == "__main__":
    app()

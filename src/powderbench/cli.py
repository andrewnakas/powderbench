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


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@app.command()
def stations():
    """List the station registry."""
    from .stations import load_stations

    for s in load_stations():
        typer.echo(f"{s.station_id:>14}  {s.resort:<28} {s.region:<18} {s.elevation_ft:>6.0f} ft")


@app.command()
def validate(submission: Path):
    """Validate a submission CSV (schema, ranges, coverage)."""
    from .validate import validate_submission

    res = validate_submission(submission)
    typer.echo(res.summary())
    raise typer.Exit(0 if res.ok else 1)


@app.command()
def build_climatology(begin: str = "2015-10-01", end: str = "2025-06-30"):
    """Rebuild the climatology table from SNOTEL history."""
    from .climatology import build_climatology as build

    build(_parse_date(begin), _parse_date(end))


@app.command()
def hindcast(
    begin: str,
    end: str,
    submission: Optional[Path] = typer.Option(None, help="CSV with round_date column"),
    team: str = typer.Option("you", help="Team name for your submission"),
    out: Optional[Path] = typer.Option(None, help="Write per-round metrics CSV here"),
):
    """Training camp: score baselines (and optionally your forecasts) on past days."""
    from .hindcast import load_hindcast_submission, run_hindcast
    from .leaderboard import aggregate

    subs = {team: load_hindcast_submission(submission)} if submission else {}
    rows = run_hindcast(_parse_date(begin), _parse_date(end), submissions=subs)
    if not len(rows):
        typer.echo("no scorable rounds in range")
        raise typer.Exit(1)
    if out:
        rows.to_csv(out, index=False)
        typer.echo(f"wrote {out}")
    board = aggregate(rows, min_rounds=1)
    cols = ["rank", "team", "rounds", "powder_score", "mae", "pinball", "brier6", "avg_coverage"]
    typer.echo("\n=== Hindcast leaderboard (unofficial — training camp) ===")
    typer.echo(board[cols].to_string(index=False))


@app.command()
def open_round(round_date: Optional[str] = typer.Option(None, help="Defaults to tomorrow UTC")):
    """Create the round manifest for a submission day."""
    from .rounds import open_round as do_open

    d = _parse_date(round_date) if round_date else date.today() + timedelta(days=1)
    path = do_open(d)
    typer.echo(f"opened round {d} -> {path}")


@app.command()
def baseline_submit(round_date: str):
    """Generate and store baseline submissions for an open round."""
    from .rounds import submit_baselines

    for team, path in submit_baselines(_parse_date(round_date)).items():
        typer.echo(f"{team}: {path}")


@app.command()
def resolve(round_date: Optional[str] = typer.Option(None, help="Resolve one round; default: all matured")):
    """Fetch observations, score submissions for matured rounds, update results."""
    from .rounds import resolve_matured, resolve_round

    if round_date:
        result = resolve_round(_parse_date(round_date))
        typer.echo(json.dumps(result, indent=1, default=str) if result else "not resolvable yet")
    else:
        for rid in resolve_matured():
            typer.echo(f"resolved {rid}")


@app.command()
def build_leaderboard():
    """Aggregate resolved rounds into results/leaderboard.json."""
    from .leaderboard import build_leaderboard as build

    out = build()
    typer.echo(f"leaderboard over {out['generated_rounds']} rounds")


@app.command()
def build_site():
    """Copy leaderboard + station data into site/data for the static site."""
    from .site import build_site as build

    for f in build():
        typer.echo(f"wrote {f}")


if __name__ == "__main__":
    app()

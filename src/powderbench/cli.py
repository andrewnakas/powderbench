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

LeagueOpt = typer.Option("stations", "--league", "-l", help="League (see data/leagues.yaml or `powderbench leagues`)")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@app.command()
def stations(league: str = LeagueOpt):
    """List a league's station registry."""
    from .stations import load_stations

    for s in load_stations(league):
        typer.echo(f"{s.station_id:>18}  {s.resort:<30} {s.region:<22} {s.elevation_ft:>6.0f} ft")


@app.command()
def validate(
    submission: Path,
    league: str = LeagueOpt,
    round_date: Optional[str] = typer.Option(None, help="Check against that round's frozen station set"),
):
    """Validate a submission CSV (schema, ranges, coverage)."""
    from .validate import validate_submission

    round_stations = None
    if round_date:
        from .leagues import get_league
        from .rounds import round_station_ids

        round_stations = round_station_ids(get_league(league), _parse_date(round_date))
    res = validate_submission(submission, league=league, round_stations=round_stations)
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

    from dataclasses import replace

    from .truth_sources import get_source

    lg = get_league(league)
    src = get_source(lg.truth_source)
    defaults = tuple(d.isoformat() for d in src.climatology)
    if src.climatology_source:
        # e.g. the resorts league has no report history yet: its reference
        # climatology is ERA5 at the resort coordinates (docs/DATA.md caveat)
        lg = replace(lg, truth_source=src.climatology_source)
    build(lg, _parse_date(begin or defaults[0]), _parse_date(end or defaults[1]))


@app.command()
def hindcast(
    begin: str,
    end: str,
    league: str = LeagueOpt,
    submission: Optional[Path] = typer.Option(None, help="CSV with round_date column"),
    team: str = typer.Option("you", help="Team name for your submission"),
    out: Optional[Path] = typer.Option(None, help="Write per-round metrics CSV here"),
    featured_only: bool = typer.Option(False, help="Score only the league's featured (headline) stations"),
):
    """Training camp: score baselines (and optionally your forecasts) on past days."""
    from .hindcast import load_hindcast_submission, run_hindcast
    from .leaderboard import aggregate
    from .leagues import get_league

    lg = get_league(league)
    subs = {team: load_hindcast_submission(submission)} if submission else {}
    rows = run_hindcast(lg, _parse_date(begin), _parse_date(end), submissions=subs, featured_only=featured_only)
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
    if not lg.in_season(d):
        typer.echo(f"{lg.name} is off-season for {d} — not opening a round")
        return
    path = do_open(lg, d)
    typer.echo(f"opened {lg.name} round {d} -> {path}")


# GitHub cron starts 30 min to 3 h late; baselines must still use only
# information available before the cutoff, so they run well ahead of it
BASELINE_LEAD_H = 3


@app.command()
def leagues(
    due: Optional[str] = typer.Option(None, help="open | baselines: only leagues whose cron slot is --hour"),
    hour: Optional[int] = typer.Option(None, help="UTC hour of the cron run (default: now)"),
):
    """List league names (one per line) — workflows loop over this instead of
    hardcoding league lists. open runs in the cutoff hour (for the round ~24h
    ahead); baselines run BASELINE_LEAD_H hours before the cutoff."""
    from .leagues import load_leagues

    from datetime import timezone

    h = datetime.now(timezone.utc).hour if hour is None else hour
    for lg in load_leagues():
        if due == "open" and lg.cutoff_hour_utc != h:
            continue
        if due == "baselines" and (lg.cutoff_hour_utc - BASELINE_LEAD_H) % 24 != h:
            continue
        typer.echo(lg.name)


def _slot_round(lg, slot_hour: int | None = None) -> date:
    """The round whose cutoff this baselines run is for. With slot_hour (the
    cron's *scheduled* hour), the answer doesn't drift when the runner starts
    late or a previous league takes a while: the slot is the most recent
    slot_hour:00 at or before now, and the round is the first cutoff after it."""
    from datetime import timezone

    now = datetime.now(timezone.utc)
    if slot_hour is None:
        anchor = now - timedelta(hours=2)
    else:
        anchor = now.replace(hour=slot_hour, minute=0, second=0, microsecond=0)
        if anchor > now:
            anchor -= timedelta(days=1)
    d = anchor.date() - timedelta(days=1)
    while lg.cutoff_utc(d) <= anchor:
        d += timedelta(days=1)
    return d


@app.command()
def baseline_submit(
    round_date: Optional[str] = typer.Argument(None, help="Default: the round with the next cutoff"),
    league: str = LeagueOpt,
    mode: str = typer.Option("live", help="live | hindcast (dry runs)"),
    slot_hour: Optional[int] = typer.Option(None, help="Scheduled UTC hour of the cron slot (pins the round)"),
):
    """Generate and store baseline submissions for an open round."""
    from .leagues import get_league
    from .rounds import submit_baselines

    lg = get_league(league)
    d = _parse_date(round_date) if round_date else _slot_round(lg, slot_hour)
    if not lg.in_season(d):
        typer.echo(f"{lg.name} is off-season for {d} — no baselines to submit")
        return
    for team, path in submit_baselines(lg, d, mode=mode).items():
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
def build_snowhistory():
    """Export the southern snow-station archive for the site's season explorer."""
    from .snowhistory import build_snowhistory_site

    files = build_snowhistory_site()
    typer.echo(f"wrote {len(files)} files" if files else "no snowhistory catalog yet")


@app.command()
def scrape_resorts(
    only: Optional[str] = typer.Option(None, help="Scrape a single resort_id"),
    dry_run: bool = typer.Option(False, help="Fetch and parse but write nothing"),
    hemisphere: Optional[str] = typer.Option(None, help="N | S: only that side's resorts (default: all)"),
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
    path = scrape_all(only=only, hemisphere=hemisphere)
    typer.echo(f"wrote {path}")


@app.command()
def open_event(
    title: str,
    start: str = typer.Option(..., help="First storm day (station-local), YYYY-MM-DD"),
    end: str = typer.Option(..., help="Last storm day, YYYY-MM-DD (window <= 7 days)"),
    league: str = LeagueOpt,
    bbox: Optional[str] = typer.Option(None, help="min_lat,min_lon,max_lat,max_lon (default: whole league)"),
    cutoff_hours_before: int = typer.Option(24, help="Hours before the league's own cutoff for `start`"),
):
    """Storm Mode: open a storm-total contest over a region."""
    from .events import open_event as do_open

    box = tuple(float(x) for x in bbox.split(",")) if bbox else None
    typer.echo(f"opened {do_open(title, league, _parse_date(start), _parse_date(end), box, cutoff_hours_before)}")


@app.command()
def event_baselines(event_id: str, mode: str = typer.Option("live", help="live | hindcast")):
    """Write the model storm-total baselines for an event."""
    from .events import load_event, submit_event_baselines

    for p in submit_event_baselines(load_event(event_id), mode):
        typer.echo(f"wrote {p}")


@app.command()
def resolve_events():
    """Score every storm event whose window has matured."""
    from .events import resolve_matured_events

    for eid in resolve_matured_events():
        typer.echo(f"resolved event {eid}")


UPSTREAM = "andrewnakas/powderbench"


def _gh(*args: str, input_text: str | None = None) -> str:
    import subprocess

    out = subprocess.run(["gh", *args], capture_output=True, text=True, input=input_text)
    if out.returncode != 0:
        raise typer.BadParameter(f"gh {' '.join(args[:3])}… failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


@app.command()
def submit(
    csv_path: Path,
    team: str = typer.Option(..., help="Team name (lowercase, hyphens) — becomes the file name"),
    league: str = LeagueOpt,
    round_date: Optional[str] = typer.Option(None, help="Default: the open round with the next cutoff"),
    dry_run: bool = typer.Option(False, help="Validate and show what would be submitted"),
):
    """Validate a forecast and open the submission pull request (needs the
    GitHub CLI `gh`, logged in). Forks the repo if you don't own it."""
    import base64
    import re

    from .leagues import get_league
    from .rounds import round_dir, round_station_ids
    from .validate import validate_submission

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", team) or team.startswith("baseline-"):
        raise typer.BadParameter("team: lowercase letters, digits and hyphens; 'baseline-' is reserved")
    lg = get_league(league)
    d = _parse_date(round_date) if round_date else _slot_round(lg)
    if not (round_dir(lg, d) / "round.json").exists():
        typer.echo(f"note: round {lg.name}/{d} isn't in your checkout — `git pull` to validate against it")
    res = validate_submission(csv_path, league=lg, round_stations=round_station_ids(lg, d))
    typer.echo(res.summary())
    if not res.ok:
        raise typer.Exit(1)
    path = f"data/submissions/{lg.name}/{d.isoformat()}/{team}.csv"
    typer.echo(f"round {lg.name}/{d} · cutoff {lg.cutoff_utc(d):%Y-%m-%d %H:%M} UTC · {path}")
    if dry_run:
        return

    login = _gh("api", "user", "-q", ".login")
    owner, name = UPSTREAM.split("/")
    repo = UPSTREAM if login == owner else f"{login}/{name}"
    if repo != UPSTREAM:
        _gh("repo", "fork", UPSTREAM, "--clone=false")
    main_sha = _gh("api", f"repos/{UPSTREAM}/git/ref/heads/main", "-q", ".object.sha")
    branch = f"submit-{team}-{lg.name}-{d.isoformat()}"
    try:
        _gh("api", f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={main_sha}")
    except typer.BadParameter:
        pass  # branch exists from an earlier submit: update the file on it
    content = base64.b64encode(Path(csv_path).read_bytes()).decode()
    args = ["api", "-X", "PUT", f"repos/{repo}/contents/{path}", "-f", f"message=Submission: {team} ({lg.name}/{d})",
            "-f", f"content={content}", "-f", f"branch={branch}"]
    try:  # updating an existing submission needs its blob sha
        args += ["-f", "sha=" + _gh("api", f"repos/{repo}/contents/{path}?ref={branch}", "-q", ".sha")]
    except typer.BadParameter:
        pass
    _gh(*args)
    head = branch if repo == UPSTREAM else f"{login}:{branch}"
    try:
        url = _gh("pr", "create", "--repo", UPSTREAM, "--head", head, "--base", "main",
                  "--title", f"Submission: {team} ({lg.name}/{d})",
                  "--body", f"Forecast for round `{lg.name}/{d}` (cutoff {lg.cutoff_utc(d):%Y-%m-%d %H:%M} UTC), "
                            f"submitted with `powderbench submit`. Coverage {res.coverage:.0%}.")
    except typer.BadParameter:
        url = "(PR already open — the file on it was updated)"
    typer.echo(f"submitted: {url}")


@app.command()
def build_site():
    """Copy leaderboards + station data into site/data for the static site."""
    from .site import build_site as build

    for f in build():
        typer.echo(f"wrote {f}")


if __name__ == "__main__":
    app()

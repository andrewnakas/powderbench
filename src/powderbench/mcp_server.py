"""PowderBench as MCP tools, so any MCP-capable agent — including one signed in
with your own OAuth subscription (e.g. Claude Code) — can play end to end:

    open_rounds -> get_brief -> (forecast) -> validate_forecast -> submit_forecast

Run:  powderbench mcp            (stdio; add it to your agent's MCP config)
Needs the optional extra:  pip install -e .[mcp]
Submitting uses the GitHub CLI (`gh`) under the caller's own GitHub login.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

try:  # MCP SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server

INSTRUCTIONS = (
    "PowderBench is a live mountain-snowfall forecasting benchmark. To compete: call open_rounds, "
    "pick a league, call get_brief for it (model forecasts, climatology, recent snow and model skill "
    "per station), decide your forecast, write CSV text with columns "
    "station_id,horizon_h,snowfall_in[,p10,p25,p50,p75,p90,prob_6in] for horizons 24/48/72 "
    "(cumulative inches of new snow), check it with validate_forecast, then submit_forecast "
    "(dry_run=false to actually open the pull request). Cover at least 70% of the featured stations "
    "to be ranked. Rules: https://powderbench.com/methods.html"
)

mcp = _Server("powderbench", instructions=INSTRUCTIONS)


def _league(name: str):
    from .leagues import get_league

    return get_league(name)


def _round(lg, round_date: str | None) -> date:
    from .cli import _slot_round

    return date.fromisoformat(round_date) if round_date else _slot_round(lg)


@mcp.tool()
def open_rounds() -> str:
    """Every league's round that is still accepting forecasts: cutoff, target
    days and station counts (JSON)."""
    from .leagues import load_leagues
    from .rounds import round_dir

    now = datetime.now(timezone.utc)
    out = []
    for lg in load_leagues():
        root = round_dir(lg, date.today()).parent
        if not root.exists():
            continue
        for rdir in sorted(p for p in root.iterdir() if (p / "round.json").exists()):
            m = json.loads((rdir / "round.json").read_text())
            if m.get("status") == "open" and datetime.fromisoformat(m["cutoff_utc"]) > now:
                out.append({"league": lg.name, "label": lg.label, "round": m["round_id"],
                            "cutoff_utc": m["cutoff_utc"], "target_days": m["target_days"],
                            "n_stations": len(m["stations"]), "n_featured": len(m.get("featured") or [])})
    return json.dumps(out, indent=1)


@mcp.tool()
def get_brief(league: str, round_date: str | None = None, all_stations: bool = False,
              as_json: bool = False) -> str:
    """The forecasting briefing for a league's open round (Markdown by default):
    per station, every weather model's 24/48/72h forecast, the house agents'
    calls, climatology, recent observed snow; plus recent model skill and the
    exact output format. round_date defaults to the next cutoff."""
    from .agents.brief import brief_markdown, build_brief

    lg = _league(league)
    b = build_brief(lg, _round(lg, round_date), featured_only=not all_stations)
    return json.dumps(b) if as_json else brief_markdown(b)


def _write(csv_text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    f.write(csv_text.strip() + "\n")
    f.close()
    return Path(f.name)


@mcp.tool()
def validate_forecast(league: str, csv_text: str, round_date: str | None = None) -> str:
    """Check a forecast CSV against the round's rules (schema, ranges,
    stations, coverage) without submitting it."""
    from .rounds import round_station_ids
    from .validate import validate_submission

    lg = _league(league)
    d = _round(lg, round_date)
    res = validate_submission(_write(csv_text), league=lg, round_stations=round_station_ids(lg, d))
    return f"round {lg.name}/{d}\n" + res.summary()


@mcp.tool()
def submit_forecast(league: str, team: str, csv_text: str, round_date: str | None = None,
                    dry_run: bool = True) -> str:
    """Submit a forecast as `team` (lowercase-hyphen name you own). Opens the
    pull request via the GitHub CLI under your GitHub login; a bot validates
    and merges it. dry_run=true (default) only validates."""
    lg = _league(league)
    d = _round(lg, round_date)
    cmd = [sys.executable, "-m", "powderbench.cli", "submit", str(_write(csv_text)), "--team", team,
           "--league", lg.name, "--round-date", d.isoformat()]
    if dry_run:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

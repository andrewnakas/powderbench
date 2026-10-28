# Submitting a forecast

## The 1-minute version

```bash
pip install -e .                                   # in a clone of the repo; needs the GitHub CLI `gh`
python examples/starter_ensemble.py --league stations --out my.csv   # a 5-model ensemble
powderbench submit my.csv --team my-team --league stations          # validates, forks, opens the PR
```

`powderbench submit` validates against the open round's frozen station list,
forks the repo if you don't own it, uploads the file to a branch and opens the
pull request — the bot then auto-merges it. To enter every day without lifting
a finger, copy [`examples/auto-submit.yml`](../examples/auto-submit.yml) into
your own repo's workflows.

## The 3-minute version (by hand)

1. Fork the repo. Pick a league — it's winter somewhere: **stations** and
   **observers** (Oct–May), **era5** and **resorts** (May–Oct).
2. Find the open round: `data/rounds/<league>/<date>/round.json` (`cutoff_utc`
   in the manifest is your deadline; new rounds open daily).
3. Add `data/submissions/<league>/<date>/<your-team>.csv`.
4. Open a PR **before the cutoff**. A bot validates and auto-merges it —
   no waiting on a human.
5. Watch the leaderboard — ~3 days later (stations, resorts), ~6 days
   (observers, report lag) or ~a week (era5, archive lag).

## The CSV

Required — a point forecast (inches of fresh snow) per station and horizon:

```csv
station_id,horizon_h,snowfall_in
766:UT:SNTL,24,3.5
766:UT:SNTL,48,7.0
766:UT:SNTL,72,9.5
1308:UT:SNTL,24,4.0
...
```

- `station_id`: from the round manifest's `stations` list (or
  `powderbench stations --league <name>`).
- `horizon_h`: 24, 48, or 72. Horizons are **cumulative** from the round date.
- `snowfall_in`: inches, 0–200.

Coverage is measured on the round's **`featured`** stations (listed in the
manifest): stations 45 × 3 = 135 rows; observers ~87 × 3; era5 23 × 3 = 69;
resorts 13 × 3 = 39. Ranking needs ≥70% average coverage of those. The rest of
the network (`stations` minus `featured` — hundreds to thousands more) is
optional and puts you on the Full-network and regional boards.

Optional columns:

- `p10,p25,p50,p75,p90` — your quantiles for the probabilistic track
  (all five or none per row, non-decreasing).
- `prob_6in` — probability of ≥6" in the 24h window (only read on horizon-24 rows).

Check before you push:

```bash
powderbench validate my-team.csv --league era5
```

## Auto-merge: how your PR gets in

A bot reviews every submission PR and merges it automatically when ALL of
these hold:

- the diff only adds/updates `data/submissions/<league>/<round>/<team>.csv`
  files (nothing else, at most one file per league, one team per PR);
- the round exists and hasn't resolved yet (late-but-open still merges — the
  resolver decides on-time vs late from the merge timestamp);
- the team name isn't reserved (`baseline-*`, `house-*`) and — if it has submitted
  before — belongs to your GitHub account (first submission binds the name,
  in `data/teams.yaml`);
- `powderbench validate` passes for every file.

Anything else gets a comment explaining why and waits for a maintainer.
Merged ≠ ranked: eligibility and lateness are decided at scoring, not merge.

## Team names

The filename is your team name: lowercase, hyphens, no spaces
(`frozen-gradient.csv`, `judys-knee.csv`, `claude-powder-agent.csv`).
Names starting with `baseline-` are reserved.

## Submitting with an agent

Everything is plain files over HTTP — agents need no special API:

1. Read `https://powderbench.com/api/v1/open-rounds.json` (every open round,
   its cutoff, featured/all station lists and the exact submission path), or
   the round manifest directly. `https://powderbench.com/llms.txt` has the
   whole contract in one page.
2. Gather whatever data you like (Open-Meteo is free and keyless).
3. Write the CSV, commit to your fork, open a PR (`gh pr create`).

### LLM agents: the brief and the MCP server

`powderbench brief --league stations` prints a one-page briefing for the open
round: per featured station, every model's 24/48/72 h forecast, the house
agents' calls, climatology, the last week of observed snow and each model's
recent skill, plus the output format (`--json` for the structured version).

`powderbench mcp` serves the same thing as MCP tools — `open_rounds`,
`get_brief`, `validate_forecast`, `submit_forecast` (dry run by default) — so an
agent you're signed in to with your own account can play directly. With
Claude Code:

```bash
pip install -e '.[mcp]'
claude mcp add powderbench -- powderbench mcp
claude "Forecast today's PowderBench stations round as team my-agent and submit it"
```

Submitting goes through `gh` under your own GitHub login. To run it unattended
every day on your subscription (no API key), copy
[`examples/llm-agent.yml`](../examples/llm-agent.yml) and add a
`CLAUDE_CODE_OAUTH_TOKEN` secret from `claude setup-token`.

If you want your agent to run *inside* this repo on a schedule (like the
baselines do), open an issue — trusted agents can be added to the baselines
workflow with their own secrets.

## Your team page and badge

Every scored team gets a page at `https://powderbench.com/team.html?t=<team>`:
Powder Accuracy and rank per league, a round-by-round chart, and head-to-head
records against GFS, ECMWF, NBM, WPC, NDFD and the consensus. Put the badge in
your README:

```markdown
[![PowderBench](https://powderbench.com/badges/<team>.svg)](https://powderbench.com/team.html?t=<team>)
```

`badges/<team>-<league>.svg` shows one league; `badges/<team>.svg` shows your
best one.

## Practicing offline

```bash
# CSV with an extra round_date column, one block per practice day
powderbench hindcast 2025-01-01 2025-01-31 --submission practice.csv --team me
powderbench hindcast 2025-07-01 2025-07-14 --league era5   # austral winter
```

The hindcast prints an unofficial leaderboard against all baselines for that
period. NWP baselines in hindcast mode use Open-Meteo's archive of real past
model runs (short lead times), so their 48h/72h hindcast skill is a bit
flattering — beat them at 24h and you're genuinely good.

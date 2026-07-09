# ❄️ PowderBench

**The live mountain-snowfall forecasting benchmark. Beat the weather models.**

Every day, PowderBench opens a round: predict fresh snowfall — 24h, 48h, and 72h
out — at **44 SNOTEL stations** near legendary Western ski zones (Alta, Jackson
Hole, Mt. Baker, Palisades Tahoe, Wolf Creek, Alyeska…). Forecasts lock at
00:00 UTC, then get scored against QC'd, real mountain snow telemetry.
Humans, ML pipelines, and AI agents all compete on one leaderboard — against
each other and against NWP baselines that submit automatically every round.

It's contamination-free by construction: you're forecasting weather that
hasn't happened yet.

## Why this is fun

- **A clear villain.** The Open-Meteo/GFS baselines are good. Climatology scores
  0 by definition; the models score ~+25 to +30. If you can out-forecast a global
  weather model at Alta, the leaderboard will say so, publicly.
- **Powder Alerts.** A probabilistic side-game: what's the chance of a 6"+ day?
- **No accounts, no keys.** Enter with a pull request. Scored by GitHub Actions.
- **Playable year-round.** The training camp scores you against any past season
  in minutes.

## Quickstart: your first forecast in 5 minutes

```bash
git clone <this-repo> && cd snowfallbenchmark
python3 -m venv .venv && .venv/bin/pip install -e .

# see the mountains
.venv/bin/powderbench stations

# 1. today's round manifest (opens daily at 00:05 UTC):
cat data/rounds/<tomorrow>/round.json

# 2. write forecasts: one row per station × horizon
#    station_id,horizon_h,snowfall_in
#    766:UT:SNTL,24,3.5
#    766:UT:SNTL,48,7.0
#    ...

# 3. check it
.venv/bin/powderbench validate my-team.csv

# 4. submit: open a PR adding data/submissions/<round>/<my-team>.csv
#    before 00:00 UTC. Done. Scores land ~3 days later.
```

### Training camp (works today, no waiting)

Score yourself against January 2025 — the baselines run automatically:

```bash
.venv/bin/powderbench hindcast 2025-01-01 2025-01-31
# add your own: --submission my.csv --team me   (CSV needs a round_date column)
```

## Scoring

| Track | Metric | Field(s) |
|---|---|---|
| **Powder Score** (headline) | `100 × (1 − MAE / MAE_climatology)`, on the same station-horizons you predicted | `snowfall_in` (required) |
| Probabilistic | Mean pinball loss over 5 quantiles | `p10,p25,p50,p75,p90` (optional) |
| Powder Alert | Brier score on P(≥6" in 24h) | `prob_6in` (optional) |

0 = climatology. Positive = real skill. The weather-model baselines sit around
+25–30. Ranking requires ≥70% coverage and 5+ rounds; QC-voided station-days
(sensor glitches) are excluded for everyone. Full details: [docs/RULES.md](docs/RULES.md).

## How it works

| When (UTC) | What |
|---|---|
| 00:05 | `open-round` workflow opens tomorrow's round |
| 23:15 | Baselines (zeros, climatology, persistence, Open-Meteo, GFS) submit |
| 00:00 | **Cutoff** — submissions after this are flagged late |
| D+3 16:00 | Round resolves against SNOTEL truth; leaderboard + site update |

- **Ground truth:** [USDA NRCS SNOTEL](https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html)
  daily snow-depth deltas, cross-checked against snow water equivalent
  ([docs/DATA.md](docs/DATA.md)).
- **Baselines:** [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), plus
  climatology built from 10 seasons of SNOTEL history.
- **Anti-cheat:** a submission counts only if it landed on `main` before the
  cutoff (GitHub sets merge timestamps; they can't be forged). Late entries are
  scored but never ranked.

## Repository map

```
src/powderbench/     the engine: clients, truth QC, scoring, rounds, leaderboard
data/stations.yaml   the 44 curated stations (API-verified)
data/rounds/         daily round manifests + resolved truth
data/submissions/    one CSV per team per round — this is where your PR goes
data/results/        per-round scores + leaderboard.json
site/                the public leaderboard site (GitHub Pages)
docs/                RULES.md · SUBMITTING.md · DATA.md
```

## Docs

- [SUBMITTING.md](docs/SUBMITTING.md) — submission format + PR walkthrough
- [RULES.md](docs/RULES.md) — cutoffs, scoring, eligibility, QC, anti-gaming
- [DATA.md](docs/DATA.md) — exactly how truth is computed, sources, licenses

## License

MIT for code. SNOTEL data is public domain (USDA); Open-Meteo data CC BY 4.0.

# ❄️ PowderBench

**The live, open benchmark for mountain snowfall forecasting. Beat the weather models.**

[![Tests](https://github.com/andrewnakas/powderbench/actions/workflows/tests.yml/badge.svg)](https://github.com/andrewnakas/powderbench/actions/workflows/tests.yml)
[![Site](https://img.shields.io/badge/live-powderbench.com-7cc4ff)](https://powderbench.com)
[![Truth dataset](https://img.shields.io/badge/%F0%9F%A4%97%20dataset-powderbench--truth-yellow)](https://huggingface.co/datasets/nakas/powderbench-truth)
[![Methodology](https://img.shields.io/badge/docs-methodology-informational)](docs/METHODOLOGY.md)
[![License: MIT](https://img.shields.io/badge/code-MIT-green)](LICENSE)
[![Cite](https://img.shields.io/badge/cite-CITATION.cff-lightgrey)](CITATION.cff)

Every day, PowderBench opens rounds in six leagues — one per kind of ground
truth: predict fresh snowfall — 24h, 48h, and 72h out — at **~2,400 quality-controlled mountain
stations** across the US West and East, Canada, the Swiss Alps, the Andes, New Zealand's
Southern Alps and Australia's Snowies. Humans, ML pipelines, and AI agents all compete on
one leaderboard — against each other and against NWP baselines that submit
automatically every round.

| League | Stations | Truth | Season | Cutoff (round D) | Resolves |
|---|---|---|---|---|---|
| **Stations** | ~1,000 snow-telemetry stations — the full SNOTEL network plus CDEC (Sierra), SNOLITE and BC pillows; 45 featured (Alta, Jackson, Baker, Cooke City…) | Real snow telemetry (NRCS SNOTEL) | **Oct–May — live now** | 00:00 UTC on D | D+3 |
| **Observers** *(trial)* | ~1,150 mountain and ski-area COOP + CoCoRaHS observers (timing-verified) plus Canada's staffed mountain stations (Whistler, Silver Star, Grouse…), ~90 featured | **Measured** new snow — a person, a snowboard and a ruler (NOAA GHCN-Daily, ECCC) | **Oct–May — live now** | 00:00 UTC on D | D+6 |
| **Resorts · North America** *(trial)* | 19 resorts' own reports: Alta, Lake Louise, Jay Peak, Wolf Creek, Mt Baker, Red Mountain, Le Massif… — and growing | Each resort's own morning snow report, archived twice daily | **Nov–Apr** | 00:00 UTC on D | D+4 |
| **Alps** *(trial)* | ~100 SLF manual study plots + IMIS stations, featured at Zermatt, Verbier, St. Moritz, Davos, Laax, Engelberg, Grindelwald… | **Measured** 24h new snow (SLF, CC BY) | **Nov–May** | 21:00 UTC on D−1 | D+4 |
| **ERA5** | 23 zones (Portillo, Las Leñas, Remarkables, Perisher…) | ERA5 model analysis (see [docs/DATA.md](docs/DATA.md)) | May–Oct | 11:00 UTC on D−1 | ~D+8 |
| **Resorts** | 13 resorts and growing across NZ / AU / Chile / Argentina (Mt Hutt, Coronet Peak, Remarkables, Tūroa, Whakapapa, Thredbo, Catedral, Chapelco, La Hoya, Las Leñas, Portillo, Valle Nevado, Antillanca) | The resort's own published snow report, archived daily (see [docs/DATA.md](docs/DATA.md)) | May–Oct | 11:00 UTC on D−1 | D+3 |

Big leagues rank everyone on their **featured** stations; forecast the rest of
the network too and you also land on the **Full network** and **regional**
boards (Wasatch, Tahoe, Colorado, Cascades…). See [docs/RULES.md](docs/RULES.md).

It's contamination-free by construction: you're forecasting weather that
hasn't happened yet. The northern leagues run Oct–May and the southern ones
May–Oct, so there is always something to forecast — and always a model to beat.

## Why this is fun

- **A whole rogues' gallery.** Every round, the world's weather models submit
  automatically: ECMWF IFS and its AI model AIFS, GFS, ICON, GEM, UKMO, NOAA's
  NBM and HRRR in the US, Météo-France AROME in the Alps — plus NOAA WPC's
  official snowfall percentiles (`baseline-wpc`) and a `baseline-consensus`
  median of them all. Beat the crowd. Climatology scores
  0 by definition; the models score ~+25 to +30. If you can out-forecast a global
  weather model at Alta, the leaderboard will say so, publicly.
- **Powder Alerts.** A probabilistic side-game: what's the chance of a 6"+ day?
- **No accounts, no keys.** Enter with a pull request. Scored by GitHub Actions.
- **Playable year-round.** The training camp scores you against any past season
  in minutes.

## Quickstart: your first forecast in 1 minute

```bash
git clone https://github.com/andrewnakas/powderbench.git && cd powderbench
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python examples/starter_ensemble.py --league stations --out my.csv
.venv/bin/powderbench submit my.csv --team my-team --league stations   # needs the GitHub CLI
```

Then make it better than the starter — that's the game. Daily autopilot:
[`examples/auto-submit.yml`](examples/auto-submit.yml).

## Quickstart by hand: your first forecast in 5 minutes

```bash
git clone https://github.com/andrewnakas/powderbench.git && cd powderbench
python3 -m venv .venv && .venv/bin/pip install -e .

# see the mountains (it's July — start with the southern-hemisphere leagues)
.venv/bin/powderbench stations --league era5

# 1. the open round manifest:
cat data/rounds/era5/<date>/round.json

# 2. write forecasts: one row per station × horizon
#    station_id,horizon_h,snowfall_in
#    portillo:CL:ERA5,24,3.5
#    portillo:CL:ERA5,48,7.0
#    ...

# 3. check it
.venv/bin/powderbench validate my-team.csv --league era5

# 4. submit: open a PR adding data/submissions/<league>/<round>/<my-team>.csv
#    before the league cutoff. Done.
```

### Training camp (works today, no waiting)

Score yourself against any past period — the baselines run automatically:

```bash
.venv/bin/powderbench hindcast 2025-01-01 2025-01-31                 # stations
.venv/bin/powderbench hindcast 2025-07-01 2025-07-14 --league era5   # era5
# add your own: --submission my.csv --team me   (CSV needs a round_date column)
```

## Training data

- **Train a model in Colab:** [`examples/house_model_colab.ipynb`](https://colab.research.google.com/github/andrewnakas/powderbench/blob/main/examples/house_model_colab.ipynb)
  — LightGBM quantile post-processing of GFS/ICON/GEM/NBM at real 1–3-day lead
  times against PowderBench truth, scored season-out exactly like the benchmark.

- **Snowpack & avalanche data:** [`nakas/powderbench-snowpack`](https://huggingface.co/datasets/nakas/powderbench-snowpack)
  on Hugging Face mirrors the open data avalanche forecasters work from —
  Météo-France SAFRAN-Crocus snowpack simulations (1958–) and ski-resort
  observer network, Norway's Varsom warning archive, SLF/EnviDat danger levels,
  SNOWPACK features and avalanche records. Each source keeps its own licence
  (built by `scripts/snowpack/harvest.py`, refreshed weekly).
- **Truth history:** every league's QC'd daily truth comes out of
  `powderbench hindcast` / `daily_truth` for any past window.

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

Daily automation, per league (all times UTC):

| League | Round opens | Baselines lock in | Cutoff | Resolution |
|---|---|---|---|---|
| Stations | 00:05 (round D = tomorrow) | 23:15 | 00:00 on D | 16:00 on D+3 |
| ERA5 | 11:05 (round D = day after tomorrow) | 10:15 | 11:00 on D−1 | 16:00 from D+8 |
| Resorts | 11:05 (round D = day after tomorrow) | 10:15 | 11:00 on D−1 | 16:00 on D+3 |

Resort snow reports are ephemeral, so a scrape cron archives them twice daily
(20:30 and 13:00 UTC) into `data/resortreports/` — resolution reads the
archive, never the live sites.

- **Ground truth:** stations — [USDA NRCS SNOTEL](https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html)
  daily snow-depth deltas cross-checked against snow water equivalent; era5 —
  ERA5 analysis (no public SH station API exists; real feeds get promoted as
  they prove stable); resorts — each resort's own published snow report,
  scraped from the resort's site with per-resort robots/ToS review
  ([docs/RESORTS.md](docs/RESORTS.md)). Details: [docs/DATA.md](docs/DATA.md).
- **Baselines:** [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), plus
  per-league climatology (10 SNOTEL seasons / 34 ERA5 years; the resorts
  league reference is ERA5 climatology at the resort coordinates until enough
  report history accumulates).
- **Anti-cheat:** a submission counts only if it landed on `main` before the
  cutoff (GitHub sets merge timestamps; they can't be forged). Late entries are
  scored but never ranked. League leaderboards never mix.

## Repository map

```
src/powderbench/       the engine: clients, truth adapters, QC, scoring, rounds
src/powderbench/resortfeeds/   resort snow-report scrapers + snapshot archive
data/leagues.yaml      league config (cutoffs, truth sources, maturity)
data/stations.yaml     81 curated stations across the three leagues
data/rounds/<league>/  daily round manifests + resolved truth
data/submissions/<league>/<round>/   one CSV per team — this is where your PR goes
data/results/<league>/ per-round scores + leaderboard.json
data/resortreports/    archived resort snow-report snapshots (resorts truth)
site/                  the public leaderboard site (GitHub Pages)
docs/                  RULES.md · SUBMITTING.md · DATA.md · RESORTS.md
```

## Docs

- [Methodology](docs/METHODOLOGY.md) — truth, QC, scoring, validation, limitations
- [Data sources & licences](docs/SOURCES.md)
- [SUBMITTING.md](docs/SUBMITTING.md) — submission format + PR walkthrough
- [RULES.md](docs/RULES.md) — cutoffs, scoring, eligibility, QC, anti-gaming
- [DATA.md](docs/DATA.md) — exactly how truth is computed, sources, licenses
- [RESORTS.md](docs/RESORTS.md) — how resort scraping works + adding a resort

## License

MIT for code. Data keep their providers' licences — public domain (USDA, NOAA),
CC BY (SLF, CoCoRaHS, Open-Meteo/ERA5), Open Government Licence – Canada, ODbL
(OpenSkiData) — see [docs/SOURCES.md](docs/SOURCES.md) for the full list and
required attributions.

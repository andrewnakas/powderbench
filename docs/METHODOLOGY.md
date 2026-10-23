# PowderBench methodology

*Version 0.3 · October 2026 · living document — every claim here is backed by
code in this repository and can be re-run.*

PowderBench is a live, public benchmark for **mountain snowfall forecasting
24–72 hours ahead**. Forecasts are submitted before a cutoff and scored days
later against quality-controlled observations. Because every round asks about
weather that has not happened yet, the benchmark is **contamination-free by
construction**: there is no answer key to leak into training data.

This document describes what is measured, how ground truth is built and
quality-controlled, how forecasts are scored, which reference forecasts every
competitor faces, how the pipeline was validated, and its known limitations.

---

## 1. Task

For each league, a **round** is named by its target date *D*. Before the
league's cutoff, a competitor submits, for every station (or any subset):

| Field | Meaning |
|---|---|
| `snowfall_in` (required) | point forecast of new snow, inches, cumulative over the horizon |
| `p10, p25, p50, p75, p90` (optional) | quantiles of the same quantity |
| `prob_6in` (optional) | probability of ≥ 6″ in the first 24 h |

Horizons are **24 h** (station-local day *D*), **48 h** (*D*…*D+1*) and **72 h**
(*D*…*D+2*). The station set of a round is frozen when the round opens
(`round.json`), so registry growth never changes an open round.

## 2. Leagues and ground truth

Each league is defined by one kind of ground truth; results never mix.

| League | Stations | Truth | Season | Cutoff (round *D*) | Resolves |
|---|---|---|---|---|---|
| **Stations** | ~1,000 (45 featured) | Automated snow telemetry: NRCS SNOTEL, SNOLITE, CDEC pillows, BC pillows | Oct–May | 00:00 UTC on *D* | *D*+3 |
| **Observers** | ~1,160 (98 featured) | **Measured** new snow: NWS COOP + CoCoRaHS (NOAA GHCN-Daily), ECCC staffed stations | Oct–May | 00:00 UTC on *D* | *D*+6 |
| **Alps** | 216 (19 featured) | **Measured** 24 h new snow (HN): SLF study plots + IMIS | Nov–May | 21:00 UTC on *D*−1 | *D*+4 |
| **Resorts · North America** | 37 resorts | Each resort's own morning snow report | Nov–Apr | 00:00 UTC on *D* | *D*+4 |
| **ERA5** | 23 points | ERA5 reanalysis (Andes, NZ, Australia) | May–Oct | 11:00 UTC on *D*−1 | ~*D*+8 |
| **Resorts · Southern Hemisphere** | 13 resorts | Each resort's own morning snow report | May–Oct | 11:00 UTC on *D*−1 | *D*+3 |

Cutoffs are set so that every station's local day *D* begins after the
cutoff. Resolution waits for the slowest truth source in the league.

### 2.1 Snow telemetry (Stations league)

Automated stations report snow depth (SNWD) and snow water equivalent (SWE).
New snow on local day *d* is the positive depth change between the end of
*d*−1 and the end of *d*: `snow24(d) = max(SNWD(d) − SNWD(d−1), 0)`.

- **SNOTEL** (NRCS AWDB, public domain): daily values with `periodRef=END`
  (local midnight). All active stations with both depth and SWE and ≥ 3 years
  of record.
- **CDEC** (California DWR): CDEC's *daily* depth is a daily mean, which
  smears storms across two days, so we read **hourly** depth/SWE and use the
  median of the 23:00/00:00/01:00 readings at local midnight. Validated
  against the nearest SNOTEL: median daily-snowfall correlation **r = 0.96**
  (13 pairs, Jan–Feb 2026). Pillows within 1.5 km of a SNOTEL are the same
  site and are excluded.
- **SNOLITE** (NRCS, depth only): see the neighbour rule in §3.
- **BC automated snow pillows** (served by AWDB as network MSNT).

### 2.2 Measured new snow (Observers and Alps leagues)

Human observers measure new snow on a snowboard once a day; SLF also derives
HN from automatic depth sensors. These are the closest thing to the quantity
skiers and forecasters care about, but they raise one subtle problem:
**observation-day attribution**. A reading is filed under the date it was
taken, yet observers read at different times — a 07:00 reading holds mostly
the previous day's snow, an evening reading the same day's. A one-day error
shifts every storm and would dominate any score.

We therefore **fit each station's day offset** (`powderbench/networks/
attribution.py`): correlate its last winter(s) of reports against ERA5 daily
snowfall at its coordinates for shifts −1/0/+1 and keep the best shift only if
its correlation is ≥ 0.4 **and** it beats the runner-up by ≥ 0.1. Where a
network's protocol documents the reading time (SLF study plots, 07:00), the
data need only confirm it. Stations that don't track storms or are ambiguous
are excluded. In the US network, 822 stations fit "next morning", 439 fit
"same day" (207 of them CoCoRaHS, contrary to a naive global rule), and 739
were excluded; correcting attribution reduced the error of every reference
forecast against this truth (GFS MAE 0.670″ → 0.655″, climatology
0.871″ → 0.836″).

Admission rules (`scripts/networks/*.py`) favour mountain and ski-area sites:
west of 100°W, ≥ 1,500 m and within 30 km of a SNOTEL site; east, ≥ 500 m and
east of 93°W; or anywhere within 15 km of an operating ski area with ≥ 2 lifts
(OpenSkiData). Stations need a solid winter record (a climatology on ≥ 60 of
the ~90 Dec–Feb days).

### 2.3 Resort snow reports (Resort leagues)

The resort leagues score against the number each resort publishes in its own
morning report — the figure skiers actually see. Strict sourcing rules:
first-party pages only (never aggregators such as OnTheSnow, OpenSnow,
Snow-Forecast, nor pages fed by them); robots.txt must allow the page; the
site's terms must not prohibit automated access; at most two polite fetches
per site per day; each enabled resort carries a dated audit stamp. Every
parser is verified against archived winter captures of the live page and
cross-checked against the page's own 48 h / 7-day figures. Reading rules:
"Trace" = 0; a range ("2–4″") = its midpoint; where the page carries a report
date, a report more than 30 h old at scrape time is archived as **stale** and
never counted (a resort that didn't update must not have yesterday's figure
credited to today). The scrape archive is append-only and committed, so every
truth value can be traced to a dated snapshot.

### 2.4 Reanalysis (ERA5 league)

For southern-hemisphere ski areas without an open station network, truth is
ERA5 daily snowfall at the resort's coordinates (Open-Meteo archive, pinned to
`era5_seamless`). This is a model analysis — smooth (~25 km) and identical for
everyone — and is disclosed as such.

## 3. Quality control

A station-day that fails QC is **voided for everyone**: it never counts for or
against any competitor. A 48 h / 72 h window is valid only if every component
day is. QC is deterministic and public (`powderbench/truth.py`,
`powderbench/networks/`).

| Rule | Applies to | Void when |
|---|---|---|
| Missing | all | a reading needed for the day is absent (blank is never treated as zero) |
| Range | depth networks | depth < 0 or > 300″ |
| Implausible | all | > 48″ in a day |
| SWE support | SNOTEL, CDEC, BC | a ≥ 6″ depth jump without an SWE rise ≥ 0.1″ |
| Neighbour support | SNOLITE (no SWE) | a ≥ 6″ jump without an SWE rise at a SNOTEL within 40 km |
| Density | CDEC | depth < SWE (physically impossible) |
| Stuck sensor | CDEC | depth unchanged ≥ 3 days while SWE rises ≥ 0.5″ |
| Spike | CDEC | depth > 10″ above both neighbouring days |
| Source QC | GHCN, ECCC | the provider's own quality flag, accumulated (multi-day) or estimated values |
| Stale | resorts | the report's own timestamp is > 30 h old |

## 4. Scoring

**Powder Score** (headline): `100 × (1 − MAE_team / MAE_climatology)`.
Climatology's MAE is computed on exactly the station-horizons the team
predicted, so partial coverage cannot cherry-pick easy stations. 0 = no better
than climatology; 100 = perfect.

The **climatology** forecast is the per-station, per-day-of-year median of the
league's own truth history (±7-day window, ≥ 30 samples) — the MAE-optimal
no-information forecast — built from each station's own record where one
exists.

Side metrics: mean **pinball loss** over the five quantiles (probabilistic
track) and the **Brier score** of `prob_6in` (Powder Alert track).

**Featured stations and tracks.** Large leagues rank everyone on a curated
*featured* set (the marquee ski zones). Forecasting the rest of the network is
optional and earns places on the *Full network* and per-*region* boards, scored
identically.

**Eligibility.** A team is ranked after ≥ 5 resolved rounds at ≥ 70 % average
coverage of featured station-horizons (for side tracks, of that track's
stations). Seasons aggregate the mean per-round score.

**Storm Mode.** Event contests over a region and a 1–7-day window score
storm totals against the summed daily truth (all days must be valid).

## 5. Reference forecasts ("baselines")

Every round, the following submit automatically, 3 hours before the cutoff (so
they never use information issued after it — GitHub's scheduler can start
jobs up to ~3 h late):

| Team | Source |
|---|---|
| `baseline-climatology` | defines 0 |
| `baseline-zeros`, `baseline-persistence` | trivial floors |
| `baseline-openmeteo` | Open-Meteo best-match blend, every station |
| `baseline-gfs`, `-ecmwf`, `-aifs`, `-icon`, `-gem`, `-ukmo` | individual global models (AIFS = ECMWF's AI model), featured stations |
| `baseline-nbm`, `-hrrr` | NOAA National Blend and HRRR (US leagues) |
| `baseline-arome` | Météo-France AROME (Alps) |
| `baseline-wpc` | NOAA WPC official snowfall percentiles + P(≥6″) (US) |
| `baseline-ndfd` | NWS forecast offices' official snow amount (NDFD), 24/48 h (US) |
| `baseline-consensus` | median of the NWP baselines (≥ 3 models) |

Each baseline is computed independently: one provider's outage never removes
the others.

**When baselines run.** GitHub's scheduler starts jobs up to about 5 h late,
and baselines are exempt from the submission deadline. So a late start must not
let them fetch forecasts issued after the cutoff. The job fires every 2 h, and a
run generates a round's baselines only when that round has none yet and its
cutoff is 2.5–9 h away. Each cutoff gets three or four chances, and a run that
starts too close to the cutoff does nothing. Baselines therefore use forecasts
at least 2.5 h older than the cutoff: if anything, a small handicap against
players who submit at the last minute.

The deadline applies to baselines and house agents like everyone else. Before
2026-10-23, baselines were exempt, and delayed jobs committed them after the
cutoff in 33 rounds:

| League | Rounds |
|---|---|
| ERA5 | 17 |
| Resorts (SH) | 10 |
| Stations | 4 |
| Observers | 2 |

Rounds already scored keep those baseline scores, noted here. Rounds scored from
2026-10-23 on, including the open ones, flag late baselines like any late entry.

**House agents** (`house-*`, `src/powderbench/agents/`) are forecasting
strategies run by the maintainers — competitors, not reference forecasts. They
run right after the baselines (2.5–9 h before the cutoff), may use only today's model
forecasts and the truth of rounds already resolved, and are labelled on the
leaderboard. `powderbench agent-backtest` replays past rounds under exactly
that rule. The agents:

- `house-blend`: a per-station skill-weighted, bias-corrected blend of the models.
- `house-analog`: k-nearest analogs. For each station and horizon it looks up the 40
  most similar resolved cases: similar model consensus, spread, climatology and season,
  preferring the same station. It forecasts the weighted distribution of what actually
  fell in those cases.
- `house-gbm`: LightGBM quantile regression (p10–p90) over every model's forecast,
  consensus, spread, climatology, location, elevation and season. It is retrained from
  the resolved history on every run.

The learned agents (analog, gbm) abstain until a league has 20 resolved rounds
and 40 snowy cases (≥ 2″) per horizon. Where today's model consensus is beyond
anything in their history, they use house-blend's values instead, because a
tree model or analog can't forecast more snow than it has seen.

ERA5 league backtest, Jul–Oct 2026. Each agent is compared with the baselines
on the same rounds:

| | Rounds | Powder Score | MAE (in) | Pinball | GFS on the same rounds | Climatology MAE |
|---|---|---|---|---|---|---|
| house-gbm | 63 | **+38.3** | 0.40 | 0.154 | −25.0 | 0.82 |
| house-analog | 63 | +37.0 | 0.40 | 0.149 | −25.0 | 0.82 |
| house-blend | 90 | +26.0 | 0.57 | 0.239 | −13.6 | 1.11 |

The raw models run high against ERA5's smoother truth, which is the bias these agents
learn. Southern resorts with `house-blend`: lowest MAE (4.23″ vs. GFS 4.37″) but a lower
mean Powder Score (+7.4 vs. +9.4). The leagues that started this season have little
resolved history, so the agents abstain (submit nothing) until they have enough cases.

## 6. Fairness and integrity

- **Deadline:** a submission counts only if its file first landed on `main`
  before the cutoff (+5 min). Merge timestamps are set by GitHub, not authors.
- **Open and deterministic:** scoring code, QC rules, truth values, all
  submissions and all results are public; anyone can recompute the
  leaderboard.
- **No accounts, no keys:** entry is a pull request; a bot validates and
  auto-merges. Team names are bound to the GitHub account that first uses them.
- **Agents welcome:** automated competitors are first-class; teams label
  themselves honestly.

## 7. Validation

Before a network goes live it must pass a **hindcast gate**: over a recent
winter fortnight, NWP baselines must clearly beat climatology and the trivial
floors must not. Results (featured stations, 2026-02-01…14):

| League | Best NWP | Consensus | Climatology | Persistence |
|---|---|---|---|---|
| Observers | +25.1 (GFS) | — | 0 | −46.8 |
| Alps | +41.1 (ICON) | +41.6 | 0 | −0.8 |

A network whose truth made the models look worse than climatology would
indicate broken truth and would not launch. Hindcast NWP uses archived
short-lead runs, so its skill is optimistic relative to live rounds.

## 8. Known limitations

- **Point vs. grid.** Station truth is a point; models are grid averages.
  Orographic gradients make every forecast's job harder at some sites — equally
  for everyone.
- **Measurement physics.** Depth-based snowfall underestimates during
  rapid settling and overestimates with wind loading; SWE checks catch the
  worst of it, not all of it.
- **Observer windows.** Observer days run morning-to-morning; rounds use local
  calendar days. Attribution aligns the bulk of each storm; a few hours remain
  misaligned at morning-reading sites.
- **Resort reports** are honest-but-imperfect self-reports and measurement
  locations vary; that is why they are a separate league.
- **ERA5** is a model analysis, not an observation; its league is clearly
  labelled.
- **Hindcast** skill is not live skill (see §7).

## 9. Data sources and licences

All sources and their licences are listed in [SOURCES.md](SOURCES.md) and on
the site's *Sources* page. Every source permits redistribution of the derived
values we publish; where terms require permission, we ask first
([OUTREACH.md](OUTREACH.md)).

## 10. Citing PowderBench

See [`CITATION.cff`](../CITATION.cff). Datasets: QC'd truth
(`huggingface.co/datasets/nakas/powderbench-truth`) and snowpack/avalanche
training data (`huggingface.co/datasets/nakas/powderbench-snowpack`).

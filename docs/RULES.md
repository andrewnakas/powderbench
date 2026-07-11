# PowderBench rules

## Leagues

Three independent leaderboards — one per kind of ground truth; results never mix:

| | Stations | ERA5 | Resorts |
|---|---|---|---|
| Points | 45 SNOTEL | 23 ERA5 resort points | 13 resorts (growing) |
| Truth | Real telemetry | Model analysis | The resort's own published snow report |
| Cutoff for round D | 00:00 UTC on D | **11:00 UTC on D−1** | **11:00 UTC on D−1** |
| Resolves | D+3, 16:00 UTC | from D+8, 16:00 UTC (ERA5 archive lag) | D+3, 16:00 UTC |
| Season | Oct 1 – Sep 30 | Apr 1 – Mar 31 | Apr 1 – Mar 31 |

Each league is named for its truth source, because the truth *is* the game:
telemetry rewards nowcasting real snow, reanalysis rewards matching a model's
smoothed view, and the resorts league scores you against whatever number the
mountain publishes in its morning report — generous stakes and all. Resort
numbers never leak into the other leagues' truth (see [DATA.md](DATA.md)).

## The round

- A round is named by its **target-start date** `D`; the first forecast day is
  station-local day `D`. Submissions lock at the league cutoff (above), plus a
  5-minute grace period.
- Targets: cumulative fresh snowfall (inches) at each registry station for
  - **24h** — station-local day `D`
  - **48h** — days `D` through `D+1`
  - **72h** — days `D` through `D+2`

## Submitting

- One CSV per team per round: `data/submissions/<league>/<D>/<team>.csv`, via pull request.
- Your team name is the filename. One team per person/bot; sockpuppets get removed.
- Required columns: `station_id, horizon_h, snowfall_in`.
  Optional: `p10,p25,p50,p75,p90` (all five or none, non-decreasing) and `prob_6in`.
- A submission is **on time** iff its file first landed on `main` before the cutoff.
  GitHub sets merge timestamps — they cannot be forged by backdating commits. Open
  PRs early; maintainers merge valid PRs as they come in.
- Late or invalid submissions are scored in the round results for your own reference
  but are excluded from all leaderboard aggregation.
- You may update your submission with another PR any time before the cutoff.

## Ground truth and QC

- Stations truth is the QC'd SNOTEL snow-depth delta; era5 truth is ERA5
  daily snowfall at the station's coordinates; resorts truth is the resort's
  archived snow report (see [DATA.md](DATA.md)).
- A station-day is **voided for everyone** when data misbehaves — stations:
  missing readings, negative/absurd depths, a >48" daily jump, or a ≥6" jump with
  no supporting SWE increase; era5: missing analysis data; resorts: no archived
  report for that day, or a >48" claim. A 48h/72h window is voided if any
  component day is.
- Voided station-horizons never count for or against anyone. No appeals needed —
  the QC code is public and deterministic.

## Scoring

- **Powder Score** (headline): `100 × (1 − MAE_you / MAE_climatology)`, where
  climatology's MAE is computed **on exactly the station-horizons you predicted** —
  partial coverage can't cherry-pick easy stations for an edge.
  0 = climatology. The NWP baselines run ~+25 to +30.
- **Probabilistic track**: mean pinball loss over the five quantiles.
- **Powder Alert track**: Brier score on the event "≥6 inches in the 24h window",
  from your `prob_6in`.
- Season aggregates are the mean of your per-round scores.

## Ranking eligibility

- Ranked teams need **≥5 resolved rounds** at **≥70% average coverage** of
  station-horizons. Everyone else is listed unranked until they qualify.
- Baselines (`baseline-*`) are ranked like anyone else. Beat them.

## Fair play

- Any information source is allowed — NWP output, satellite, webcams, your knees.
  The cutoff is the only wall: no information from after 00:00 UTC.
- Automated/agent submissions are welcome and encouraged. Label your team honestly
  (e.g. `gpt-powder-agent`); the community may ask for a description of your method.
- Attempting to exploit scoring, QC, or timing bugs gets the round voided for you;
  repeat offenders are banned. Found a bug? Open an issue — bug reporters get
  eternal glory in the README.

## Season

- Stations seasons run October 1 – September 30 (Western US snow year);
  era5 and resorts seasons April 1 – March 31. Season leaderboards reset;
  all-time results stay archived in `data/results/<league>/`.

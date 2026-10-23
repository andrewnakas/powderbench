# PowderBench rules

## Leagues

Independent leaderboards — one per kind of ground truth; results never mix:

| | Stations | Observers (trial) | Alps (trial) | ERA5 | Resorts |
|---|---|---|---|---|---|
| Points | ~1,000 SNOTEL + CDEC + SNOLITE + BC pillows (45 featured) | ~1,150 US COOP + CoCoRaHS mountain and ski-area observers + Canadian ECCC stations (~90 featured) | ~100 SLF study plots + IMIS (Swiss Alps) | 23 ERA5 resort points | 13 resorts (growing) |
| Truth | Snow-depth telemetry (Δdepth, SWE-checked) | **Measured** new snow on an observer's snowboard | Measured 24h new snow (SLF) | Model analysis | The resort's own published snow report |
| Cutoff for round D | 00:00 UTC on D | 00:00 UTC on D | **21:00 UTC on D−1** | **11:00 UTC on D−1** | **11:00 UTC on D−1** |
| Resolves | D+3, 16:00 UTC | D+6, 16:00 UTC (report latency) | D+4, 16:00 UTC | from D+8, 16:00 UTC (ERA5 archive lag) | D+3, 16:00 UTC |
| Season | Oct 1 – Sep 30 | Oct 1 – Sep 30 | Oct 1 – Sep 30 (rounds Nov–May) | Apr 1 – Mar 31 | Apr 1 – Mar 31 |

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
  PRs early; a bot verifies and **auto-merges valid submissions within minutes**
  (see SUBMITTING.md). PRs the bot can't clear go to manual review.
- A team name belongs to the GitHub account that first submits under it
  (`data/teams.yaml`); submissions for someone else's team are never auto-merged.
- Late or invalid submissions are scored in the round results for your own reference
  but are excluded from all leaderboard aggregation.
- You may update your submission with another PR any time before the cutoff.

## Ground truth and QC

- Stations truth is the QC'd SNOTEL snow-depth delta; observers truth is the
  measured new snow (GHCN-Daily `SNOW`) from the next morning's report; era5 truth is ERA5
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
  partial coverage can't cherry-pick easy stations for an edge. (A station whose
  climatology is still being built drops out of the comparison for everyone.)
  0 = climatology. The NWP baselines run ~+25 to +30.
- **Probabilistic track**: mean pinball loss over the five quantiles.
- **Powder Alert track**: Brier score on the event "≥6 inches in the 24h window",
  from your `prob_6in`.
- Season aggregates are the mean of your per-round scores.

## Featured stations, the full network, and regions

Big leagues carry two kinds of station:

- **Featured** stations (the marquee ski zones — big dots on the map) are the
  **headline leaderboard**. Coverage, eligibility and the headline Powder Score
  are all measured on them.
- **Network** stations (the rest of the league) are optional. Forecast them and
  you also appear on the side tracks:
  - **Full network** — every station in the league;
  - **Region · …** — one board per region (Wasatch, Tahoe, Colorado…), scored
    on the stations you forecast there.

Side tracks use the same Powder Score, eligibility bar and QC; they're where
regional specialists and full-network ML models get to shine. A round's station
set is frozen when the round opens (`round.json`), so registry growth never
changes an open round.

## Storm Mode (event contests)

When a big storm lines up, a maintainer opens a **storm-total event**
(`powderbench open-event`): a region of one league, a window of 1–7
station-local days, and a cutoff (by default 24 h before the league's normal
cutoff for the first storm day). Predict the **window total** at each station:
`data/submissions/events/<event_id>/<team>.csv` with `station_id,snowfall_in`
(optional `p10..p90`). Truth is the league's QC'd daily truth summed over the
window — a station counts only if *every* day is valid. Scored with MAE and the
Powder Score against a climatology storm (the sum of each day's climatological
mean), alongside each weather model's storm total and their consensus. The same
on-time rule applies (first landed on `main` before the cutoff). Event PRs are
merged by a maintainer rather than the auto-merge bot.

## Ranking eligibility

- Ranked teams need **≥5 resolved rounds** at **≥70% average coverage** of
  featured station-horizons (for a side track: of that track's stations).
  Everyone else is listed unranked until they qualify.
- Baselines (`baseline-*`) are ranked like anyone else. Beat them.
- House agents (`house-*`) are maintainer-run forecasting strategies, held to
  the same cutoff (they run with the baselines), labelled on the leaderboard and
  open-source. `baseline-` and `house-` team names are reserved.

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

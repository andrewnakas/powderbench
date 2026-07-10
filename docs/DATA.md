# Data: sources, truth definition, QC

## Ground truth: SNOTEL (northern league)

Source: USDA NRCS AWDB REST API
(`https://wcc.sc.egov.usda.gov/awdbRestApi`), public domain, no key.

We fetch **daily** values with `periodRef=END`, so `SNWD(d)` — snow depth,
inches — is the reading at the **end** of station-local day `d` (verified
against hourly data: the daily value equals the 00:00 reading of `d+1`).

**Fresh snowfall attributed to day `d`:**

```
snow24(d) = max(SNWD(d) − SNWD(d−1), 0)
```

This aligns with how forecast models report local-day snowfall totals
(confirmed against the Jan 3–5, 2025 Alta storm on both sources). Settling and
melt make the raw delta negative on no-snow days — clamped to zero. Horizons
are cumulative sums: `h48(D) = snow24(D) + snow24(D+1)`, etc.

Snow depth deltas are a noisy but honest measure of what skiers actually care
about. Its known quirks (settling during storms under-measures very wet storms,
wind loading, sensor noise of ±1") apply to every competitor equally.

## QC: voided station-days

A station-day is voided — excluded from scoring for everyone — when
(`src/powderbench/truth.py`):

| Rule | Condition |
|---|---|
| `missing` | either day's SNWD reading is absent |
| `bad_depth` | depth < 0 or > 300" |
| `implausible_jump` | daily delta > 48" |
| `no_swe_support` | delta ≥ 6" while SWE rose < 0.1" (snow pillow disagrees) |
| `swe_missing_on_big_jump` | delta ≥ 6" but SWE data is missing |

A 48h/72h window is valid only if every component day is valid. Typical
midwinter void rates are a few percent.

## Ground truth: ERA5 analysis (southern trial league)

There is no southern-hemisphere SNOTEL equivalent with a stable public API
(we probed: NIWA is key-gated with negotiated access; Chile's DGA is
session-based JSP portals; the CL/AR Observatorio Andino is an R Shiny
websocket app). The trial league therefore scores against **ERA5 reanalysis
daily snowfall** at each resort's coordinates, fetched from Open-Meteo's
keyless archive API (`archive-api.open-meteo.com`, CC BY 4.0).

Honest caveats, disclosed everywhere the trial appears:

- ERA5 is a **model analysis**, not a snow stake. At ~25 km grid scale it
  mutes maritime peaks (NZ/AU totals read low) and misses microclimates.
  High-resolution models over-forecast relative to ERA5's smoothed totals —
  calibrating your forecasts to the truth's scale is part of the game (July
  2025 hindcast: GFS +49 Powder Score, the high-res best_match blend −34).
- Truth is pinned to pure reanalysis (`models=era5_seamless`). The archive's
  default "best_match" blends in the same operational data the forecast
  archive serves, which would make the NWP baseline literally identical to
  truth (we hit this: MAE 0.000 before pinning).
- ERA5 assimilates observations that also feed the live forecast models, so
  the NWP baselines are structurally correlated with the truth — treat their
  southern Powder Scores as a hard, slightly flattered target.
- The archive lags real time by ~5 days, so rounds resolve from D+8.
- QC is missing-data-only (no sensor glitches to void).

## Observation feeds (southern)

`src/powderbench/obsfeeds.py` records best-effort real observations next to
model truth (never blocking resolution). Once a feed runs clean for a station,
that station's `truth_source` in `stations.yaml` can be flipped to promote it
to real truth. Recon status (2026-07):

| Feed | Source | Status |
|---|---|---|
| `snowyhydro` | Spencers Creek daily snow depth (site 00003, 1,830 m, Perisher/Thredbo massif), recovered from Snowy Hydro's daily HYPLOT chart PDF via vector-path extraction (~±2 cm) | **Live** — mapped to `perisher:AU:ERA5` |
| `ina` | Argentina INA a5 open JSON API (`alerta.ina.gob.ar/a5`), 47 real snow-level telemetry stations incl. NIV Las Leñas (~1 km from our point) and NIV Túnel Internacional (Cristo Redentor pass, near Portillo) | **Implemented; upstream stale** — public sync ends mid-2024; lights up automatically if it resumes. 2022–24 data validates ERA5. |
| `niwa` | NZ Snow & Ice Network via DataHub API | Activates when `NIWA_API_KEY`/`NIWA_CUSTOMER_ID` secrets exist |
| `dga` | Chile DGA hourly nivometric telemetry | Disabled — real data exists but every public front (JSP portals, Shiny app, Angular observatorio) is app/session-gated |

Resort snow reports are **not** used: aggregators prohibit scraping and
marketing totals are inflated and gameable.

## The stations

`data/stations.yaml`: 45 active SNOTEL stations (northern) chosen for (a)
proximity to iconic ski terrain, (b) long records, (c) geographic spread —
Wasatch, Colorado Rockies, Tetons, Beartooths, Montana, Idaho, Cascades,
Sierra, New Mexico, Chugach. All SNOTEL metadata comes from the API, not
hand-entry. Plus 23 southern ERA5 resort points (Chile, Argentina, NZ,
Australia) with hand-curated coordinates.

Station id formats: `<id>:<state>:SNTL` (e.g. `766:UT:SNTL` = Snowbird) and
`<slug>:<country>:ERA5` (e.g. `portillo:CL:ERA5`).

## Climatology

`data/climatology/<league>.csv` — northern from water years 2016–2025 of
SNOTEL history; southern from ERA5 1991–2025 (`powderbench build-climatology
--league <name>`). For each station × day-of-year (±7-day circular window,
pooled across years): mean, quantiles (p10–p90) of the 24h / 48h / 72h
snowfall distributions, and the empirical frequency of ≥6" days. The
**median** is climatology's point forecast (MAE-optimal no-skill reference).

## NWP baselines: Open-Meteo

[Open-Meteo](https://open-meteo.com/) (CC BY 4.0, no key):

- **Live rounds**: `/v1/forecast` at each station's coordinates and elevation,
  daily `snowfall_sum`, models `best_match` and `gfs_seamless`, submitted 45
  minutes before the cutoff.
- **Hindcast**: `historical-forecast-api.open-meteo.com` — the archive of what
  models actually predicted at the time (available from ~2021). Lead times in
  the archive are short, so hindcast NWP skill at 48h/72h is optimistic;
  live-round NWP baselines are the true benchmark.

## Layout of generated data

```
data/leagues.yaml                        league config
data/rounds/<league>/<D>/round.json      manifest: cutoff, stations, target days
data/rounds/<league>/<D>/truth.csv       QC'd truth written at resolution
data/submissions/<league>/<D>/<team>.csv all submissions for round D
data/results/<league>/rounds/<D>.json    per-team metrics + QC counts
data/results/<league>/leaderboard.json   season + last-30 aggregates
data/obs/<league>/                       observation-feed reference data
data/cache/                              API response cache (gitignored)
```

## The exhibition week

Rounds 2025-03-01 … 2025-03-07 in the repo were resolved retroactively
(baselines via the hindcast archive) so the site and docs have real data to
show. They're clearly pre-season and won't count toward any season leaderboard.

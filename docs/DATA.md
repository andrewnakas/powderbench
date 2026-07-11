# Data: sources, truth definition, QC

## Ground truth: SNOTEL (stations league)

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

## Ground truth: ERA5 analysis (era5 league)

There is no southern-hemisphere SNOTEL equivalent with a stable public API
(we probed: NIWA is key-gated with negotiated access; Chile's DGA is
session-based JSP portals; the CL/AR Observatorio Andino is an R Shiny
websocket app). The era5 league therefore scores against **ERA5 reanalysis
daily snowfall** at each resort's coordinates, fetched from Open-Meteo's
keyless archive API (`archive-api.open-meteo.com`, CC BY 4.0).

Honest caveats, disclosed everywhere the league appears:

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
  era5-league Powder Scores as a hard, slightly flattered target.
- The archive lags real time by ~5 days, so rounds resolve from D+8.
- QC is missing-data-only (no sensor glitches to void).

## Observation feeds (era5 league)

`src/powderbench/obsfeeds.py` records best-effort real observations next to
model truth (never blocking resolution). Once a feed runs clean for a station,
that station's `truth_source` in `stations.yaml` can be flipped to promote it
to real truth. Recon status (2026-07):

| Feed | Source | Status |
|---|---|---|
| `snowyhydro` | Spencers Creek daily snow depth (site 00003, 1,830 m, Perisher/Thredbo massif), recovered from Snowy Hydro's daily HYPLOT chart PDF via vector-path extraction (~±2 cm) | **Live** — mapped to `perisher:AU:ERA5` |
| `ina` | Argentina INA a5 open JSON API (`alerta.ina.gob.ar/a5`), 47 real snow-level telemetry stations incl. NIV Las Leñas (~1 km from our point) and NIV Túnel Internacional (Cristo Redentor pass, near Portillo) | **Implemented; upstream stale** — public sync ends mid-2024; lights up automatically if it resumes. 2022–24 data validates ERA5. |
| `niwa` | NZ Snow & Ice Network via DataHub API | Activates when `NIWA_API_KEY`/`NIWA_CUSTOMER_ID` secrets exist. Per the DataHub Non-Commercial Use Licence, raw values are never committed to this repo or published — only stored privately at scoring time; published results carry Earth Sciences NZ attribution and their required disclaimer |
| `dga` | Chile DGA hourly nivometric telemetry | Disabled — real data exists but every public front (JSP portals, Shiny app, Angular observatorio) is app/session-gated |

A fifth feed, `resorts`, mirrors the resorts-league archive (below) onto the
matching era5 points — a public, running measure of how resort-claimed totals
compare with reanalysis.

## Ground truth: resort snow reports (resorts league)

The resorts league scores against **what the mountain itself reports**: each
resort's published snow report, scraped from the resort's own site by
`src/powderbench/resortfeeds/` and archived in this repo.

Why this is its own league and never truth elsewhere: resort numbers are
marketing-adjacent — measured at generous stakes, rounded up, occasionally
corrected. That makes them unusable as truth for the stations/era5 leagues.
Inside a dedicated league the bias is harmless: every competitor is scored
against the same published number, and forecasting *what the resort will
report* is a legitimate, well-defined game. The `resorts` obs feed keeps the
receipts by publishing resort-claimed totals next to ERA5 reanalysis.

**The ephemerality problem.** A resort's "24h snowfall" disappears when
tomorrow's report replaces it, so truth can never be re-fetched. A scrape cron
(`.github/workflows/scrape-resorts.yml`, 20:30 UTC for Oceania mornings and
13:00 UTC for South America mornings) archives every report as an immutable
snapshot CSV under `data/resortreports/raw/`, failures included — the archive
is both the truth source and the public audit trail. Resolution reads only
the committed archive.

**Date attribution.** A report scraped between one morning refresh and the
next describes snow that fell on the *previous* local calendar day (the same
end-of-day convention as SNOTEL's `periodRef=END`):

```
attributed = (local_scrape_time − report_hour_local).date() − 1 day
```

Two scrapes landing on the same attributed day are deduped read-side keeping
the earliest (closest after the morning refresh). Resorts that publish only a
cumulative season total (e.g. NZSki) yield daily snowfall as day-over-day
deltas, clamped at zero — a day after an archive gap can't be attributed and
is voided.

**QC (resorts league):**

| Rule | Condition |
|---|---|
| `missing` | no archived report covers that station-day (scrape failed, site down, robots disallow) |
| `implausible_jump` | reported 24h total > 48" |

**Politeness and consent.** Aggregators (OnTheSnow, Snow-Forecast, …) are
never scraped — their ToS prohibit it. Only individual resort sites are used,
each onboarded with a robots.txt + terms review recorded in the registry's
`verified` stamp (`src/powderbench/resortfeeds/registry.py`); the scraper
re-checks robots.txt on every run, identifies itself with a descriptive
User-Agent (`PowderBench/0.1 (+https://powderbench.com; …)`), spaces requests
per host, and touches each site at most twice a day. If a resort objects, its
spec is disabled and its station voids from that day forward. Per-resort
status (2026-07-10):

| Resort | Endpoint | Verdict |
|---|---|---|
| Mt Hutt, Coronet Peak, The Remarkables (NZSki) | own weather-app JSON (azurefd.net) | **Live** — robots OK, no ToS scraping clause; season-total deltas |
| Tūroa | own server-rendered page (pureturoa.nz) | **Live** — robots OK, no ToS scraping clause; 'Last 24hrs' stat |
| Whakapapa | own SSR page `/report` (embedded app-state JSON) | **Live** — robots OK, no ToS scraping clause; per-location `snow24Hours` |
| Thredbo | own XML feed `/feeds/snow-report/` | **Live** — robots OK, no ToS scraping clause; literal `snow24Hours` |
| Cerro Catedral, Chapelco, La Hoya | operator's parte-diario API (Vía Bariloche / BusPlus, exactly these 3 centros) | **Live** — no robots restrictions, no ToS scraping clause; per-sector `nieveUltimas24` |
| Portillo | own server-rendered conditions table | **Live** — robots OK, no ToS page found; season-to-date deltas (Hotel area) |
| Valle Nevado | own server-rendered report page | **Live** — robots OK, no scraping clause in /es/legal; mountain-wide 24h |
| Antillanca | own WordPress REST endpoint (`wp-json/antillanca/v1/parte-diario`) | **Live** — robots OK; `nieve_24h` + depths + updated stamp |
| Las Leñas | own server-rendered conditions table | **Live (self-activating)** — table currently publishes '-' placeholders → `no_report` until they resume |
| Mt Buller | public JSON API found (api.mtbuller.com.au) | **Disabled pending permission** — ToS restricts republication without written approval |
| Cardrona, Treble Cone | — | **Unusable**: site snow data is aggregator-fed (OpenSnow) |
| Perisher, Falls Creek, Hotham | — | **Unusable** (conservative): Vail-owned, automated access treated as prohibited |
| Cerro Castor | — | **Unusable for snowfall**: publishes per-sector depth only (depth-delta mode possible later) |
| El Colorado | — | **Unusable**: publishes no snowfall numbers |
| La Parva | — | **Disabled (stale)**: clean structure but report frozen at 2026-01-19 |
| Caviahue, Nevados de Chillán, Corralco | — | Recon incomplete: page broken / client-side rendering |

Adding a resort: [RESORTS.md](RESORTS.md).

**Climatology caveat.** The resorts league has no report history yet, so its
Powder Score reference is ERA5 climatology at the resort coordinates. Resort
reports read systematically higher than ERA5, so early Powder Scores will look
flattered vs climatology — identically for every team, hence fair. The
climatology will be rebuilt from actual report history after a season or two.

## The stations

`data/stations.yaml`: 45 active SNOTEL stations (stations league) chosen for (a)
proximity to iconic ski terrain, (b) long records, (c) geographic spread —
Wasatch, Colorado Rockies, Tetons, Beartooths, Montana, Idaho, Cascades,
Sierra, New Mexico, Chugach. All SNOTEL metadata comes from the API, not
hand-entry. Plus 23 era5 resort points (Chile, Argentina, NZ, Australia) with
hand-curated coordinates, and 13 resorts-league points (12 mirror era5
coordinates; La Hoya stands alone).

Station id formats: `<id>:<state>:SNTL` (e.g. `766:UT:SNTL` = Snowbird),
`<slug>:<country>:ERA5` (e.g. `portillo:CL:ERA5`), and
`<slug>:<country>:RESORT` (e.g. `thredbo:AU:RESORT`).

## Climatology

`data/climatology/<league>.csv` — stations from water years 2016–2025 of
SNOTEL history; era5 from ERA5 1991–2025; resorts from ERA5 1991–2025 at the
resort coordinates, pending real report history (`powderbench
build-climatology --league <name>`). For each station × day-of-year (±7-day circular window,
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
data/resortreports/raw/                  archived resort snow-report snapshots
data/cache/                              API response cache (gitignored)
```

## The exhibition week

Rounds 2025-03-01 … 2025-03-07 in the repo were resolved retroactively
(baselines via the hindcast archive) so the site and docs have real data to
show. They're clearly pre-season and won't count toward any season leaderboard.

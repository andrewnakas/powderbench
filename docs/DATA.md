# Data: sources, truth definition, QC

## Ground truth: SNOTEL

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

## The stations

`data/stations.yaml`: 44 active SNOTEL stations chosen for (a) proximity to
iconic ski terrain, (b) long records, (c) geographic spread — Wasatch, Colorado
Rockies, Tetons, Montana, Idaho, Cascades, Sierra, New Mexico, Chugach. All
metadata (coordinates, elevations) comes from the API, not hand-entry.

Station triplet format: `<id>:<state>:SNTL`, e.g. `766:UT:SNTL` = Snowbird.

## Climatology

`data/climatology/climatology.csv` — built from water years 2016–2025
(`powderbench build-climatology`). For each station × day-of-year (±7-day
circular window, pooled across years): mean, quantiles (p10–p90) of the 24h /
48h / 72h snowfall distributions, and the empirical frequency of ≥6" days.
The **median** is climatology's point forecast (MAE-optimal no-skill reference).

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
data/rounds/<D>/round.json      manifest: cutoff, stations, target days
data/rounds/<D>/truth.csv       QC'd truth written at resolution
data/submissions/<D>/<team>.csv all submissions for round D
data/results/rounds/<D>.json    per-team metrics + QC counts for round D
data/results/leaderboard.json   season + last-30 aggregates
data/cache/                     API response cache (gitignored)
```

## The exhibition week

Rounds 2025-03-01 … 2025-03-07 in the repo were resolved retroactively
(baselines via the hindcast archive) so the site and docs have real data to
show. They're clearly pre-season and won't count toward any season leaderboard.

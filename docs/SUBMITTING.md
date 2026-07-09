# Submitting a forecast

## The 3-minute version

1. Fork the repo. Pick a league — it's winter somewhere: **northern**
   (Oct–May) or **southern** (Jun–Oct, trial).
2. Find the open round: `data/rounds/<league>/<date>/round.json` (`cutoff_utc`
   in the manifest is your deadline; new rounds open daily).
3. Add `data/submissions/<league>/<date>/<your-team>.csv`.
4. Open a PR **before the cutoff**. CI validates it immediately.
5. Watch the leaderboard — ~3 days later (northern) or ~a week (southern,
   ERA5 archive lag).

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

- `station_id`: from `data/stations.yaml` (or `powderbench stations --league <name>`).
- `horizon_h`: 24, 48, or 72. Horizons are **cumulative** from the round date.
- `snowfall_in`: inches, 0–200.

Cover every station × 3 horizons for full coverage (northern: 45 × 3 = 135
rows; southern: 23 × 3 = 69). You can skip some, but ranking needs ≥70%
average coverage.

Optional columns:

- `p10,p25,p50,p75,p90` — your quantiles for the probabilistic track
  (all five or none per row, non-decreasing).
- `prob_6in` — probability of ≥6" in the 24h window (only read on horizon-24 rows).

Check before you push:

```bash
powderbench validate my-team.csv --league southern
```

## Team names

The filename is your team name: lowercase, hyphens, no spaces
(`frozen-gradient.csv`, `judys-knee.csv`, `claude-powder-agent.csv`).
Names starting with `baseline-` are reserved.

## Submitting with an agent

Everything is plain files over HTTP — agents need no special API:

1. Read the round manifest (raw GitHub URL).
2. Gather whatever data you like (Open-Meteo is free and keyless).
3. Write the CSV, commit to your fork, open a PR (`gh pr create`).

If you want your agent to run *inside* this repo on a schedule (like the
baselines do), open an issue — trusted agents can be added to the baselines
workflow with their own secrets.

## Practicing offline

```bash
# CSV with an extra round_date column, one block per practice day
powderbench hindcast 2025-01-01 2025-01-31 --submission practice.csv --team me
powderbench hindcast 2025-07-01 2025-07-14 --league southern   # austral winter
```

The hindcast prints an unofficial leaderboard against all baselines for that
period. NWP baselines in hindcast mode use Open-Meteo's archive of real past
model runs (short lead times), so their 48h/72h hindcast skill is a bit
flattering — beat them at 24h and you're genuinely good.

# Per-station gates

Each file is a per-station truth gate report: every station's daily truth
correlated with a weather model's daily snowfall for the same station-local
day (`r_0`), and with the truth shifted a day either way (`r_minus1`,
`r_plus1`). `suggested_offset` and `verdict` are the decision relative to the
station's default `day_offset`.

## nordic.csv, japan.csv, world.csv — direct ECMWF IFS gates

These files are `scripts/nwp_gate.py` reports: every station's daily truth over
winter 2025-26 (2025-12-01..2026-03-15) correlated with the direct ECMWF IFS
daily snowfall for the same station-local day (`r_0`), and with the truth
shifted a day either way (`r_minus1`, `r_plus1`). Every row was run with the
station at its network's default `day_offset` (the generator's), so
`suggested_offset` and `verdict` are the decision relative to that default.

- `nordic.csv` (SMHI, FMI, MET Norway; depth-derived truth) is **applied** by
  the generators in `scripts/networks/{smhi,fmi,frost}.py` through
  `scripts/networks/gate.py`:
  - a `fail` (best allowed shift below r 0.4) is dropped;
  - a suggested offset equal to the network's default, or one less, is used;
  - any other suggested offset is dropped as misdated. A reading can be
    dated by its own day or by the day before, never later. The default is
    1 for depth rises and DWD's morning board, and 0 for boards dated by the
    day the snow fell (ČHMÚ, Météo-France `HNEIGEF`).
  - Stations missing from the file are not gated yet and keep the default.
  - SMHI and FMI were re-run after the change (2026-11-20): 93 of 93 pass at
    their new offsets, median r 0.59.
- `japan.csv` (JMA; snowfall measured as the JST-day sum of hourly depth
  rises) is a **record only**. Its dating is fixed by the measurement. Its
  weak stations are sea-effect coasts where the 25 km model is poor, and
  those are worth keeping.
- The world league's GHCNh drops are in `scripts/networks/ghcnh.py`
  (`NWP_GATE_FAILED`).
- `world.csv` (DWD outside the Alps, Météo-France, ČHMÚ, GHCNh, ECCC) is
  **applied** by `scripts/networks/{dwd,meteofrance,eccc_depth}.py`
  (`dwd.py --world`).
  GHCNh's three drops (Chopok, Lomnický štít, Puntijarka, from the first
  world gate run) are also in `scripts/networks/ghcnh.py`'s `NWP_GATE_FAILED`.

## stations.csv — BC snow stations (stations league)

- `stations.csv` (BC snow stations read from BC's own feed: pillows,
  `truth_source: bcasws`, and depth-only stations, `bcasws_depth`) is written
  by `scripts/hindcast_gate.py` and **applied** by
  `scripts/networks/bc_asws_direct.py`, which keeps only `pass` rows (a
  station not in the file yet is held back). `--truth-source` and `--merge`
  gate a subset and keep the other rows.
  - Model: ECMWF IFS 9 km, the runs ECMWF actually made (short lead), from
    Open-Meteo's historical-forecast archive at the station's elevation.
  - Seasons 2024, 2025 and 2026 (Oct 1 – May 31 each), with per-season
    `r_0_<season>` / `n_<season>` and the pooled `r_*`.
  - `pass`: pooled `r_0` ≥ 0.40, every season ≥ 60 valid days and `r_0` ≥
    0.30, and no other shift beats shift 0 by 0.10 (depth-delta truth: the
    day is fixed by the measurement, so a better shift means misdated data).
  - Run 2026-11-23 on 51 candidates: 44 pass (median r_0 0.72), 7 fail (no
    snow at 85 m, one broken winter, five without a 2023-24 or 2024-25
    record); none misdated.
  - Run 2026-11-23 on the 4 depth-only candidates with an SWE pillow within
    40 km (truth through SNOLITE's neighbour rule): 3 pass (Loch Lomand 0.79,
    Crocker Creek 0.72, Gray Creek Lower 0.63), Kaslo Airport fails (0.31).
    55 rows in all.

## observers.csv — more observers (observers league)

GHCN-D SNOW observers outside `data/stations/ghcnd.csv`, written by
`scripts/observers_gate.py` and **applied** by
`scripts/networks/ghcnd_wide.py`, which lists the `pass` rows in
`data/stations/ghcnd_wide.csv` (a candidate not in the file yet is held back).
`route` says why a station was a candidate: `wide` (the looser admission
rules of `ghcnd_wide.py`) or `rejudge` (admitted by `ghcnd.py`'s rule at
mountain elevation, but dropped by its 2-winter ERA5 day-offset fit on
2026-10-20).

- Model: ECMWF IFS 9 km, the runs ECMWF actually made, read from Open-Meteo's
  open-data archive on AWS (`s3://openmeteo`, CC BY 4.0) — the files the
  historical-forecast API serves, so no API calls (`scripts/om_archive.py`,
  the reader from PR #876). Daily snowfall as the API computes it: its
  terrain-matched cell, its elevation rule, 0.7 cm per mm, station-local
  days. Checked against the API's own answers at 10 BC stations over the
  same three seasons: daily r 0.983–0.998 (median 0.996), season totals
  0.98–1.03 of the API's (median 1.000).
- Truth: the station's own reports as the league reads them (no report =
  missing, never 0; trace = 0; NCEI quality flag or > 48" = void).
- Seasons 2024, 2025 and 2026 (Oct 1 – May 31 each).
- `day_offset` is **set here**: the reports are correlated with the model at
  report offsets −1, 0, +1 and +2 (`r_by_offset`). As in
  `networks/attribution.py`, the best must beat every other by 0.10 and be
  +1 (morning observer) or 0 (evening); otherwise the verdict is
  `ambiguous` (no clear day) or `misdated`. `r_minus1` / `r_0` / `r_plus1`
  are relative to the offset chosen.
- `pass`, at that offset: the `stations.csv` rule — pooled `r_0` ≥ 0.40,
  every season ≥ 60 valid days and `r_0` ≥ 0.30. A station without enough
  reports in every season fails before any model read (`n` 0).
- Then `ghcnd_wide.py` applies `ghcnd.py`'s climatology pass (a solid
  Dec–Feb record) and, on the `wide` route only, a mean of ≥ 40" of new snow
  per Oct–May season in the station's own climatology. It records both
  numbers per gate pass in `observers_climatology.csv` (`winter_doys`,
  `season_in`, `verdict`) and removes the climatology rows of the stations it
  drops, so re-runs judge those from this record.
- Run 2026-11-23 on 2,160 candidates (1,674 wide, 486 rejudge): 836 pass
  (738 wide, 98 rejudge; median `r_0` 0.69), 326 ambiguous, 3 misdated,
  995 fail (571 with too few reports). Climatology pass: 477 kept (385 wide,
  92 rejudge); 135 thin records, 224 wide under 40".

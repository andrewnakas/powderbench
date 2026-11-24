# Per-station gates

Each file is a per-station truth gate report: every station's daily truth
correlated with a weather model's daily snowfall for the same station-local
day (`r_0`), and with the truth shifted a day either way (`r_minus1`,
`r_plus1`). `suggested_offset` and `verdict` are the decision relative to the
station's default `day_offset`.

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

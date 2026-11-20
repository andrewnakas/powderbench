# Per-station NWP gates

Each file is a `scripts/nwp_gate.py` report: every station's daily truth over
winter 2025-26 (2025-12-01..2026-03-15) correlated with the direct ECMWF IFS
daily snowfall for the same station-local day (`r_0`), and with the truth
shifted a day either way (`r_minus1`, `r_plus1`). Every row was run with the
station at its network's default `day_offset` (the generator's), so
`suggested_offset` and `verdict` are the decision relative to that default.

- `nordic.csv` (SMHI, FMI, MET Norway; depth-derived truth) is **applied** by
  the generators in `scripts/networks/{smhi,fmi,frost}.py` through
  `scripts/networks/gate.py`:
  - a `fail` (best allowed shift below r 0.4) is dropped;
  - a suggested offset of 0 or 1 is used;
  - any other suggested offset is dropped as misdated. A morning reading
    can be dated by its own morning or by the day before, never later.
  - Stations missing from the file are not gated yet and keep the default.
  - SMHI and FMI were re-run after the change (2026-11-20): 93 of 93 pass at
    their new offsets, median r 0.59.
- `japan.csv` (JMA; snowfall measured as the JST-day sum of hourly depth
  rises) is a **record only**. Its dating is fixed by the measurement. Its
  weak stations are sea-effect coasts where the 25 km model is poor, and
  those are worth keeping.
- The world league's GHCNh drops are in `scripts/networks/ghcnh.py`
  (`NWP_GATE_FAILED`).

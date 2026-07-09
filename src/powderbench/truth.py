"""Ground truth: QC'd daily snowfall from SNOTEL snow-depth deltas.

Daily values are fetched with periodRef=END, so SNWD(d) is the snow depth at
the END of station-local day d. New snow during day d is therefore
snow24(d) = max(SNWD(d) - SNWD(d-1), 0), which lines up with forecast models'
local-day snowfall totals.

QC voids a station-day for everyone (it never counts for or against any
competitor) rather than trying to repair sensor readings.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from . import HORIZONS

MAX_PLAUSIBLE_SNOW24_IN = 48.0  # beyond world-record territory for these sites
SWE_SUPPORT_SNOW_IN = 6.0  # a snowfall this big must show up in SWE
SWE_SUPPORT_MIN_IN = 0.1
MAX_DEPTH_IN = 300.0

QC_OK = "ok"
QC_MISSING = "missing"
QC_BAD_DEPTH = "bad_depth"
QC_IMPLAUSIBLE = "implausible_jump"
QC_NO_SWE_SUPPORT = "no_swe_support"
QC_SWE_MISSING = "swe_missing_on_big_jump"


def daily_snowfall(obs: pd.DataFrame) -> pd.DataFrame:
    """obs: station_id, date, SNWD, WTEQ (daily, periodRef=END).

    Returns: station_id, date, snow24, valid, qc_flag — one row per
    station-day in the interior of each station's date range.
    """
    out = []
    for station_id, grp in obs.groupby("station_id"):
        grp = grp.set_index("date").sort_index()
        full = pd.date_range(grp.index.min(), grp.index.max(), freq="D").date
        grp = grp.reindex(full)
        snwd, wteq = grp["SNWD"], grp["WTEQ"]
        for d in full[1:]:
            prev = d - timedelta(days=1)
            s0, s1 = snwd.get(prev), snwd.get(d)
            w0, w1 = wteq.get(prev), wteq.get(d)
            flag = QC_OK
            if pd.isna(s0) or pd.isna(s1):
                flag = QC_MISSING
            elif s0 < 0 or s1 < 0 or s0 > MAX_DEPTH_IN or s1 > MAX_DEPTH_IN:
                flag = QC_BAD_DEPTH
            else:
                delta = s1 - s0
                if delta > MAX_PLAUSIBLE_SNOW24_IN:
                    flag = QC_IMPLAUSIBLE
                elif delta >= SWE_SUPPORT_SNOW_IN:
                    if pd.isna(w0) or pd.isna(w1):
                        flag = QC_SWE_MISSING
                    elif w1 - w0 < SWE_SUPPORT_MIN_IN:
                        flag = QC_NO_SWE_SUPPORT
            valid = flag == QC_OK
            out.append(
                {
                    "station_id": station_id,
                    "date": d,
                    "snow24": max(s1 - s0, 0.0) if valid else float("nan"),
                    "valid": valid,
                    "qc_flag": flag,
                }
            )
    return pd.DataFrame(out, columns=["station_id", "date", "snow24", "valid", "qc_flag"])


def window_truth(daily: pd.DataFrame, target_day: date, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """Cumulative snowfall truth per station and horizon, starting at target_day.

    horizon 24 = day 1 total, 48 = days 1-2, 72 = days 1-3. A window is valid
    only if every component day passed QC.

    Returns: station_id, horizon_h, truth_in, valid.
    """
    rows = []
    for station_id, grp in daily.groupby("station_id"):
        grp = grp.set_index("date")
        for h in horizons:
            days = [target_day + timedelta(days=i) for i in range(h // 24)]
            present = [d for d in days if d in grp.index]
            valid = len(present) == len(days) and all(grp.loc[d, "valid"] for d in days)
            truth = sum(grp.loc[d, "snow24"] for d in days) if valid else float("nan")
            rows.append(
                {"station_id": station_id, "horizon_h": h, "truth_in": truth, "valid": valid}
            )
    return pd.DataFrame(rows, columns=["station_id", "horizon_h", "truth_in", "valid"])

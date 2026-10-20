"""Per-station observation-day attribution for observer networks.

Observers file a reading under the date they took it, but they read the board
at different times of day (morning COOP/CoCoRaHS -> the snow fell the day
before; evening observers -> same day), and networks differ. Getting this
wrong silently shifts every storm by a day — the worst possible error for a
24h benchmark. So each station's offset is *fitted*: correlate its raw daily
reports against ERA5 daily snowfall at its coordinates (station-local days)
for shifts -1/0/+1 and keep the best, only when it's clearly best.

    offset = k  means  truth for local day D  <-  the report dated D+k
"""

from __future__ import annotations

from datetime import date

import pandas as pd

OFFSETS = (-1, 0, 1)
MIN_CORR = 0.4  # the station has to actually track storms
MIN_MARGIN = 0.1  # ... and one shift has to win clearly
MIN_DAYS = 60


def fit_offsets(reports: pd.DataFrame, era5: pd.DataFrame, allowed: tuple[int, ...] = OFFSETS,
                expected: dict[str, int] | None = None) -> dict[str, dict]:
    """reports: station_id, date, snow_in (raw, as dated by the network)
    era5: station_id, date, snowfall_in (station-local days)
    allowed: shifts that are physically possible for the network (a winning
    shift outside it means the record is misdated -> no fit).
    expected: {station_id: k} where the network's protocol documents the
    reading time (e.g. SLF study plots at 07:00). Then the data only has to
    *confirm* it — k must still be the best shift and clear MIN_CORR — and
    the margin rule (for when we're guessing) is waived.
    -> {station_id: {"offset": k | None, "corr": {k: r}, "n": days}}"""
    out = {}
    e_by = {sid: g.set_index(pd.to_datetime(g["date"]))["snowfall_in"] for sid, g in era5.groupby("station_id")}
    for sid, g in reports.groupby("station_id"):
        obs = g.set_index(pd.to_datetime(g["date"]))["snow_in"].astype(float)
        mod = e_by.get(sid)
        if mod is None:
            continue
        corr, n = {}, 0
        for k in OFFSETS:
            shifted = obs.copy()
            shifted.index = shifted.index - pd.Timedelta(days=k)  # report D+k -> day D
            joined = pd.concat([shifted, mod], axis=1, join="inner").dropna()
            n = max(n, len(joined))
            if len(joined) >= MIN_DAYS and joined.iloc[:, 0].std() > 0 and joined.iloc[:, 1].std() > 0:
                corr[k] = round(float(joined.iloc[:, 0].corr(joined.iloc[:, 1])), 3)
        offset = None
        if corr:
            ranked = sorted(corr.items(), key=lambda kv: -kv[1])
            best_k, best_r = ranked[0]
            runner = ranked[1][1] if len(ranked) > 1 else -1.0
            documented = (expected or {}).get(sid)
            clear = best_r - runner >= MIN_MARGIN or best_k == documented
            if best_r >= MIN_CORR and clear and best_k in allowed:
                offset = best_k
        out[sid] = {"offset": offset, "corr": corr, "n": n}
    return out


def winters(last_season_end_year: int, n: int = 2) -> list[tuple[date, date]]:
    """Nov-Mar windows of the last n winters (ending in the given year)."""
    return [(date(y - 1, 11, 1), date(y, 3, 31)) for y in range(last_season_end_year - n + 1, last_season_end_year + 1)]

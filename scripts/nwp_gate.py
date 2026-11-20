"""Per-station truth gate against the direct ECMWF IFS forecast.

For every station of a league, over a window (a whole winter is best), this
correlates the network's daily truth with ECMWF's daily snowfall for the same
station-local day, and with the truth shifted one day either way:

  shift -1 best  -> the value dated D already holds day D's snow  (offset - 1)
  shift +1 best  -> day D's snow shows up a day later              (offset + 1)

A new day_offset is suggested only when another shift beats shift 0 by
--margin. A station fails the gate when even its best shift correlates below
--min-r: for depth-derived networks that is the signature of wind-blown or
settling-dominated depth (summits), not snowfall. Networks that measure new
snow directly (JMA, ČHMÚ observers) can fall below it where the 25 km model is
simply poor (sea-effect coasts), so read the report before dropping those.

It needs no Open-Meteo calls (run with PB_OPENMETEO=0):

  PB_OPENMETEO=0 python scripts/nwp_gate.py nordic 2025-12-01 2026-03-15 --out gate_nordic.csv
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from powderbench import baselines, nwp
from powderbench.leagues import get_league
from powderbench.stations import load_stations
from powderbench.truth_sources import daily_truth

SHIFTS = (-1, 0, 1)


def forecasts(league, stations, begin: date, end: date, model: str) -> pd.DataFrame:
    """date x station daily snowfall from the run each round would have used."""
    frames, day = [], begin
    while day <= end:
        as_of = baselines.nwp_as_of(league, day, "hindcast")
        frames.append(nwp.daily_snowfall(stations, day, day, model, as_of, live=False))
        day += timedelta(days=1)
    return pd.concat(frames).pivot_table(index="date", columns="station_id", values="snowfall_in")


def correlations(truth: pd.Series, fc: pd.Series) -> dict[int, tuple[float, int]]:
    out = {}
    for sh in SHIFTS:
        t = truth.copy()
        t.index = [d - timedelta(days=sh) for d in t.index]  # truth dated D+sh vs forecast day D
        j = pd.concat([fc, t], axis=1, keys=["f", "t"]).dropna()
        ok = len(j) > 10 and j["f"].std() > 0 and j["t"].std() > 0
        out[sh] = (float(j["f"].corr(j["t"])) if ok else float("nan"), len(j))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("league")
    ap.add_argument("begin", type=date.fromisoformat)
    ap.add_argument("end", type=date.fromisoformat)
    ap.add_argument("--model", default="ecmwf_ifs")
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--min-r", type=float, default=0.40)
    ap.add_argument("--featured", action="store_true", help="featured stations only")
    ap.add_argument("--out", help="report CSV")
    a = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    league = get_league(a.league)
    stations = list(load_stations(league.name, featured_only=a.featured))
    ids = [s.station_id for s in stations]
    tr = daily_truth(league, ids, a.begin - timedelta(days=2), a.end + timedelta(days=2))
    tr = tr[tr["valid"]].pivot_table(index="date", columns="station_id", values="snow24")
    fc = forecasts(league, stations, a.begin, a.end, a.model)

    rows = []
    for s in stations:
        if s.station_id not in tr or s.station_id not in fc:
            rows.append({"station_id": s.station_id, "name": s.name, "verdict": "no data"})
            continue
        c = correlations(tr[s.station_id], fc[s.station_id])
        r = {sh: v[0] for sh, v in c.items()}
        best = max((sh for sh in SHIFTS if np.isfinite(r[sh])), key=lambda sh: r[sh], default=0)
        shift = best if best != 0 and r[best] - r[0] >= a.margin else 0
        current = s.day_offset if s.day_offset is not None else 1
        r_used = r[shift]
        rows.append({
            "station_id": s.station_id, "name": s.name, "tier": s.tier or "featured", "n": c[0][1],
            "r_minus1": round(r[-1], 3), "r_0": round(r[0], 3), "r_plus1": round(r[1], 3),
            "day_offset": current, "suggested_offset": current + shift,
            "truth_in": round(float(tr[s.station_id].reindex(fc.index).sum()), 1),
            "model_in": round(float(fc[s.station_id].sum()), 1),
            "verdict": "pass" if np.isfinite(r_used) and r_used >= a.min_r else "fail",
        })
    rep = pd.DataFrame(rows)
    if a.out:
        rep.to_csv(a.out, index=False)
    moved = rep[rep.get("suggested_offset", rep.get("day_offset")) != rep.get("day_offset")]
    print(f"{league.name} {a.begin}..{a.end} vs {a.model}: {len(rep)} stations, "
          f"pass {int((rep.verdict == 'pass').sum())}, fail {int((rep.verdict == 'fail').sum())}, "
          f"offset changes {len(moved)}, median r_0 {rep['r_0'].median():.2f}")
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        print(rep.to_string(index=False))


if __name__ == "__main__":
    main()

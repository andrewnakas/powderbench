"""Per-station truth gate against archived model runs (a hindcast).

For every station of a league (or only those listed in a station CSV), over
whole seasons (Oct 1 .. May 31), this correlates the network's daily truth
with ECMWF IFS (9 km) daily snowfall for the same station-local day (`r_0`),
and with the truth shifted a day either way (`r_minus1`, `r_plus1`):

  shift -1 best  -> the value dated D already holds day D's snow
  shift +1 best  -> day D's snow shows up a day later

The model series are the runs ECMWF actually made (short lead), from
Open-Meteo's historical-forecast archive at the station's elevation — the
same archive the hindcast baselines read. Same report as the direct-NWP
gate (data/gates/README.md), per season and pooled.

Verdict (depth-delta truth, whose day is fixed by the measurement):
  pass      pooled r_0 >= --min-r, every season has >= --min-days valid days
            and r_0 >= --min-season-r (a broken winter can't hide in the
            pool), and no other shift beats shift 0 by --margin
  misdated  another shift beats shift 0 by --margin
  fail      anything else (no data counts as fail)

  python scripts/hindcast_gate.py stations --stations data/stations/bc_asws_direct.csv \\
      --season 2024 --season 2025 --season 2026 --out data/gates/stations.csv

A 243-day request for 25 points counts as ~435 calls against Open-Meteo's
free tier (600/min, 5,000/h, 10,000/day), so requests are paced a minute
apart. Responses are cached (data/cache/openmeteo): re-runs are free.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from powderbench import openmeteo  # noqa: E402
from powderbench.leagues import get_league  # noqa: E402
from powderbench.stations import load_stations  # noqa: E402
from powderbench.truth_sources import daily_truth  # noqa: E402

SHIFTS = (-1, 0, 1)


def season_window(season: int) -> tuple[date, date]:
    return date(season - 1, 10, 1), date(season, 5, 31)


def model_daily(stations, begin: date, end: date, model: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """date x station snowfall (in), and each station's time zone (Open-Meteo timezone=auto)."""
    rows, zones = [], {}
    for i in range(0, len(stations), openmeteo.BATCH_SIZE):
        batch = stations[i : i + openmeteo.BATCH_SIZE]
        params = {
            "latitude": ",".join(f"{s.latitude:.5f}" for s in batch),
            "longitude": ",".join(f"{s.longitude:.5f}" for s in batch),
            "elevation": ",".join(f"{s.elevation_ft * 0.3048:.0f}" for s in batch),
            "daily": "snowfall_sum", "timezone": "auto", "models": model,
            "start_date": begin.isoformat(), "end_date": end.isoformat(),
        }
        payload = openmeteo._get(openmeteo.HISTORICAL_URL, params, cacheable=True)
        for s, res in zip(batch, payload if isinstance(payload, list) else [payload]):
            zones[s.station_id] = res.get("timezone", "")
            for day, cm in zip(res["daily"]["time"], res["daily"]["snowfall_sum"]):
                rows.append((s.station_id, date.fromisoformat(day), None if cm is None else cm / openmeteo.CM_PER_INCH))
    df = pd.DataFrame(rows, columns=["station_id", "date", "snowfall_in"])
    return df.pivot_table(index="date", columns="station_id", values="snowfall_in"), zones


def correlations(truth: pd.Series, fc: pd.Series) -> dict[int, tuple[float, int]]:
    out = {}
    for sh in SHIFTS:
        t = truth.copy()
        t.index = [d - timedelta(days=sh) for d in t.index]  # truth dated D+sh vs model day D
        j = pd.concat([fc, t], axis=1, keys=["f", "t"]).dropna()
        ok = len(j) > 10 and j["f"].std() > 0 and j["t"].std() > 0
        out[sh] = (float(j["f"].corr(j["t"])) if ok else float("nan"), len(j))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("league")
    ap.add_argument("--season", type=int, action="append", required=True,
                    help="season by its end year (2025 = Oct 2024 .. May 2025); repeat")
    ap.add_argument("--stations", help="a station CSV: gate only the league's stations listed in it")
    ap.add_argument("--model", default="ecmwf_ifs")
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--min-r", type=float, default=0.40)
    ap.add_argument("--min-season-r", type=float, default=0.30)
    ap.add_argument("--min-days", type=int, default=60)
    ap.add_argument("--pace", type=float, default=61.0, help="seconds between uncached Open-Meteo requests")
    ap.add_argument("--out", help="report CSV")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    openmeteo.MIN_INTERVAL_S = a.pace

    league = get_league(a.league)
    stations = list(load_stations(league.name))
    if a.stations:
        with open(a.stations, encoding="utf-8") as f:
            only = {r["station_id"] for r in csv.DictReader(f)}
        stations = [s for s in stations if s.station_id in only]
    ids = [s.station_id for s in stations]
    seasons = sorted(set(a.season))

    tr_parts, fc_parts, zones = [], [], {}
    for season in seasons:
        b, e = season_window(season)
        tr = daily_truth(league, ids, b - timedelta(days=2), e + timedelta(days=2))
        tr_parts.append(tr[tr["valid"]].pivot_table(index="date", columns="station_id", values="snow24"))
        fc, z = model_daily(stations, b, e, a.model)
        fc_parts.append(fc)
        zones.update(z)
        print(f"season {season}: truth and {a.model} loaded", flush=True)

    rows = []
    for s in stations:
        sid = s.station_id
        per, n_ok = {}, True
        for season, tr, fc in zip(seasons, tr_parts, fc_parts):
            if sid in tr and sid in fc:
                c = correlations(tr[sid], fc[sid])
                per[season] = (c[0][0], c[0][1])
            else:
                per[season] = (float("nan"), 0)
        t_all = pd.concat([tr[sid] for tr in tr_parts if sid in tr]) if any(sid in tr for tr in tr_parts) else pd.Series(dtype=float)
        f_all = pd.concat([fc[sid] for fc in fc_parts if sid in fc]) if any(sid in fc for fc in fc_parts) else pd.Series(dtype=float)
        t_all = t_all[~t_all.index.duplicated()]
        c = correlations(t_all, f_all) if len(t_all) and len(f_all) else {sh: (float("nan"), 0) for sh in SHIFTS}
        r = {sh: v[0] for sh, v in c.items()}
        best = max((sh for sh in SHIFTS if np.isfinite(r[sh])), key=lambda sh: r[sh], default=0)
        shift = best if best != 0 and r[best] - (r[0] if np.isfinite(r[0]) else -1) >= a.margin else 0
        n_ok = all(n >= a.min_days for _, n in per.values())
        season_ok = all(np.isfinite(v) and v >= a.min_season_r for v, _ in per.values())
        if shift != 0:
            verdict = "misdated"
        elif np.isfinite(r[0]) and r[0] >= a.min_r and n_ok and season_ok:
            verdict = "pass"
        else:
            verdict = "fail"
        row = {"station_id": sid, "name": s.name, "tier": s.tier or "featured", "timezone": zones.get(sid, ""),
               "n": c[0][1], "r_minus1": round(r[-1], 3), "r_0": round(r[0], 3), "r_plus1": round(r[1], 3)}
        for season, (v, n) in per.items():
            row[f"r_0_{season}"] = round(v, 3)
            row[f"n_{season}"] = n
        current = s.day_offset if s.day_offset is not None else 0
        idx = f_all.index.intersection(t_all.index)
        row.update({"day_offset": current, "suggested_offset": current + shift,
                    "truth_in": round(float(t_all.reindex(idx).sum()), 1),
                    "model_in": round(float(f_all.reindex(idx).sum()), 1), "verdict": verdict})
        rows.append(row)
    rep = pd.DataFrame(rows)
    if a.out:
        rep.to_csv(a.out, index=False)
    print(f"{league.name} seasons {seasons} vs {a.model}: {len(rep)} stations, "
          f"pass {int((rep.verdict == 'pass').sum())}, fail {int((rep.verdict == 'fail').sum())}, "
          f"misdated {int((rep.verdict == 'misdated').sum())}, median r_0 {rep['r_0'].median():.2f}")
    with pd.option_context("display.width", 250, "display.max_rows", 500):
        print(rep.to_string(index=False))


if __name__ == "__main__":
    main()

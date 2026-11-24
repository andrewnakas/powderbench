"""Hindcast gate for observer stations (GHCN-D SNOW, observers league) with no
Open-Meteo API calls: archived ECMWF IFS 9 km daily snowfall read from
Open-Meteo's open-data archive on AWS (scripts/om_archive.py, the files the
historical-forecast API serves), against each station's own reports over whole
seasons (Oct 1 .. May 31).

Truth: GHCN-D SNOW as the league reads it (powderbench/networks/ghcnd.py): no
report = missing (never 0), trace = 0, any NCEI quality flag or a value < 0 or
> 48 in = void; day D <- the report dated D + day_offset.

Day offset, set here: the reports are correlated with the model at report
offsets -1, 0, +1 and +2 (pooled over the seasons). As in
networks/attribution.fit_offsets, the best offset must beat every other by
>= --margin and be +1 (morning observer) or 0 (evening); otherwise the
station is `ambiguous` (no clear day) or `misdated` (a clear best outside
0/+1).

Pass, at that offset (the stations-league gate's rule, data/gates/README.md):
pooled r >= --min-r, every season >= --min-days valid days and r >=
--min-season-r. Otherwise `fail` (a station without enough reports in every
season is failed before any model read).

Columns as data/gates/stations.csv: r_minus1 / r_0 / r_plus1 are relative to
the chosen day_offset; r_by_offset lists every report offset; route says why
the station was a candidate (scripts/networks/ghcnd_wide.py).

    python scripts/observers_gate.py candidates.csv --season 2024 --season 2025 --season 2026 \\
        --out data/gates/observers.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import om_archive  # noqa: E402
from powderbench import truth  # noqa: E402
from powderbench.networks import ghcnd  # noqa: E402
from powderbench.stations import data_dir  # noqa: E402

OFFSETS = (-1, 0, 1, 2)
ALLOWED = (0, 1)
DOMAIN = "ecmwf_ifs"


def season_window(season: int) -> tuple[date, date]:
    return date(season - 1, 10, 1), date(season, 5, 31)


def reports(ids: list[str], begin: date, end: date) -> dict[str, pd.Series]:
    """Valid reports by the date they are filed under, per station."""
    raw = ghcnd.fetch_snow(ids, begin, end)
    ok = raw[(raw["qflag"] == "") & (raw["snow_in"] >= 0) & (raw["snow_in"] <= truth.MAX_PLAUSIBLE_SNOW24_IN)]
    return {sid: g.set_index("date")["snow_in"].sort_index() for sid, g in ok.groupby("station_id")}


def model(stations: pd.DataFrame, season: int) -> tuple[pd.DataFrame, dict]:
    """date x station_id inches; cached per season under data/cache/omgate."""
    b, e = season_window(season)
    path = data_dir() / "cache" / "omgate" / f"{DOMAIN}_{season}.csv.gz"
    info_path = path.with_name(f"{DOMAIN}_{season}_info.csv")
    have = pd.read_csv(path, index_col=0, parse_dates=False) if path.exists() else pd.DataFrame()
    info = pd.read_csv(info_path, index_col=0).to_dict("index") if info_path.exists() else {}
    todo = stations[~stations["station_id"].isin(have.columns)]
    if len(todo):
        sts = [{"station_id": r.station_id, "latitude": r.latitude, "longitude": r.longitude,
                "elevation_m": round(r.elevation_ft * 0.3048)} for r in todo.itertuples()]
        for k in range(0, len(sts), 100):  # save as we go: a long read resumes
            frame, inf = om_archive.daily_snowfall(sts[k:k + 100], b, e, DOMAIN)
            frame.index = [d.isoformat() for d in frame.index]
            have = pd.concat([have, frame], axis=1)
            info.update(inf)
            path.parent.mkdir(parents=True, exist_ok=True)
            have.to_csv(path)
            pd.DataFrame.from_dict(info, orient="index").to_csv(info_path)
            print(f"season {season}: model at {min(k + 100, len(sts))}/{len(sts)} stations", flush=True)
    have.index = [date.fromisoformat(d) for d in have.index]
    return have, info


def corr(t: pd.Series, f: pd.Series) -> tuple[float, int]:
    j = pd.concat([f, t], axis=1, keys=["f", "t"]).dropna()
    ok = len(j) > 10 and j["f"].std() > 0 and j["t"].std() > 0
    return (float(j["f"].corr(j["t"])) if ok else float("nan")), len(j)


def aligned(rep: pd.Series, k: int, b: date, e: date) -> pd.Series:
    """Truth for days [b, e] at report offset k: day D <- report dated D + k."""
    s = rep[(rep.index >= b + timedelta(days=k)) & (rep.index <= e + timedelta(days=k))]
    return pd.Series(s.to_numpy(), index=[d - timedelta(days=k) for d in s.index], dtype=float)


def judge(r: pd.Series, fc: dict[int, pd.Series], windows: dict[int, tuple[date, date]], margin: float = 0.10,
          min_r: float = 0.40, min_season_r: float = 0.30, min_days: int = 60) -> dict:
    """One station: its reports by filing date (r) against the model's daily
    snowfall per season (fc) -> day_offset, correlations and verdict."""
    seasons = sorted(windows)
    per = {k: {s: corr(aligned(r, k, *windows[s]), fc[s]) for s in seasons} for k in OFFSETS}
    f_all = pd.concat([fc[s] for s in seasons])
    pooled = {k: corr(pd.concat([aligned(r, k, *windows[s]) for s in seasons]), f_all) for k in OFFSETS}
    rk = {k: v[0] for k, v in pooled.items() if np.isfinite(v[0])}
    best = max(rk, key=rk.get) if rk else 1
    runner = max((v for k, v in rk.items() if k != best), default=-1.0)
    clear = bool(rk) and rk[best] - runner >= margin
    at = per[best]
    if not rk:  # no correlation at any offset (e.g. never any snow)
        verdict = "fail"
    elif clear and best not in ALLOWED:
        verdict = "misdated"
    elif not clear:
        verdict = "ambiguous"
    elif rk[best] >= min_r and all(n >= min_days for _, n in at.values()) \
            and all(np.isfinite(v) and v >= min_season_r for v, _ in at.values()):
        verdict = "pass"
    else:
        verdict = "fail"
    t = pd.concat([aligned(r, best, *windows[s]) for s in seasons])
    idx = t.index.intersection(f_all.dropna().index)
    out = {"n": pooled[best][1], "r_minus1": round(rk.get(best - 1, np.nan), 3),
           "r_0": round(rk.get(best, np.nan), 3), "r_plus1": round(rk.get(best + 1, np.nan), 3)}
    for s in seasons:
        out[f"r_0_{s}"] = round(at[s][0], 3)
        out[f"n_{s}"] = at[s][1]
    out.update({"day_offset": best, "suggested_offset": best,
                "truth_in": round(float(t.reindex(idx).sum()), 1), "model_in": round(float(f_all.reindex(idx).sum()), 1),
                "verdict": verdict, "r_by_offset": " ".join(f"{k:+d}:{rk[k]:.2f}" for k in OFFSETS if k in rk)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("candidates", help="CSV: station_id, name, latitude, longitude, elevation_ft[, route]")
    ap.add_argument("--season", type=int, action="append", required=True)
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--min-r", type=float, default=0.40)
    ap.add_argument("--min-season-r", type=float, default=0.30)
    ap.add_argument("--min-days", type=int, default=60)
    ap.add_argument("--out", required=True)
    ap.add_argument("--merge", action="store_true", help="keep the --out file's rows for stations not gated now")
    a = ap.parse_args()

    cands = pd.read_csv(a.candidates)
    if "route" not in cands:
        cands["route"] = ""
    seasons = sorted(set(a.season))
    windows = {s: season_window(s) for s in seasons}
    ids = list(cands["station_id"])
    rep = reports(ids, windows[seasons[0]][0] - timedelta(days=2), windows[seasons[-1]][1] + timedelta(days=3))
    print(f"truth: {len(rep)} of {len(ids)} stations have valid reports", flush=True)

    # enough reports in every season at the default (morning) offset, with a little slack for the shift
    def enough(sid):
        r = rep.get(sid)
        return r is not None and all(len(aligned(r, 1, b, e)) >= a.min_days - 5 for b, e in windows.values())

    cands["enough"] = [enough(s) for s in cands["station_id"]]
    fc, info = {}, {}
    for s in seasons:
        fc[s], inf = model(cands[cands["enough"]], s)
        info.update(inf)

    rows = []
    for c in cands.itertuples():
        sid = c.station_id
        row = {"station_id": sid, "name": c.name, "route": c.route, "tier": "network",
               "timezone": info.get(sid, {}).get("zone", "")}
        if not c.enough:
            rows.append({**row, "n": 0, "verdict": "fail", "note": "too few reports"})
            continue
        row.update(judge(rep[sid], {s: fc[s][sid] for s in seasons}, windows, a.margin, a.min_r,
                         a.min_season_r, a.min_days))
        row["cell_dz_m"] = info.get(sid, {}).get("dz_m", "")
        rows.append(row)

    rep_df = pd.DataFrame(rows)
    out = rep_df
    if a.merge and Path(a.out).exists():
        old = pd.read_csv(a.out)
        out = pd.concat([old[~old["station_id"].isin(rep_df["station_id"])], rep_df], ignore_index=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)
    v = rep_df["verdict"].value_counts().to_dict()
    print(f"observers seasons {seasons} vs {DOMAIN} (open-data archive): {len(rep_df)} stations, {v}; "
          f"median r_0 of passes {rep_df.loc[rep_df.verdict == 'pass', 'r_0'].median():.2f}")
    for route, g in rep_df.groupby("route"):
        print(f"  {route or '-'}: {g['verdict'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()

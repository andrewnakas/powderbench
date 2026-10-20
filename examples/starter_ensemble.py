"""PowderBench starter model: a multi-model ensemble with honest uncertainty.

Pulls daily snowfall from several weather models (Open-Meteo, free, no key)
at each featured station of a league's open round, then submits:
  - snowfall_in : ensemble median           (the point forecast)
  - p10..p90    : spread across the models  (probabilistic track)
  - prob_6in    : share of models >= 6" in 24h, shrunk toward climatology-ish
                  odds so a single wild model can't make it 0 or 1

It's deliberately simple — the baselines already include each model and
their consensus, so beating the leaderboard needs *your* idea on top (bias
correction from the station's history, elevation adjustment, snow-ratio
physics, ML on the training camp...). Usage:

    python examples/starter_ensemble.py --league stations --out my.csv
    powderbench submit my.csv --team my-team --league stations
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://powderbench.com/api/v1/open-rounds.json"
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "ukmo_seamless"]
CM_PER_IN = 2.54


def open_round(league: str) -> dict:
    """The open round with the earliest cutoff (from the site API, or a local checkout)."""
    try:
        rounds = [r for r in requests.get(API, timeout=60).json()["open_rounds"] if r["league"] == league]
        r = sorted(rounds, key=lambda r: r["cutoff_utc"])[0]
        return requests.get(r["manifest_url"], timeout=60).json()
    except Exception:
        local = sorted(Path("data/rounds", league).glob("*/round.json"))
        manifests = [json.loads(p.read_text()) for p in local]
        return sorted([m for m in manifests if m["status"] == "open"], key=lambda m: m["cutoff_utc"])[-1]


def stations(league: str, ids: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(requests.get(f"https://powderbench.com/data/stations-{league}.json", timeout=60).json())
    return df[df["station_id"].isin(ids)]


def model_daily(st: pd.DataFrame, model: str, start: date) -> pd.DataFrame:
    rows = []
    for i in range(0, len(st), 25):
        b = st.iloc[i : i + 25]
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=120, params={
            "latitude": ",".join(f"{x:.4f}" for x in b.latitude),
            "longitude": ",".join(f"{x:.4f}" for x in b.longitude),
            "elevation": ",".join(f"{x * 0.3048:.0f}" for x in b.elevation_ft),
            "daily": "snowfall_sum", "timezone": "auto", "models": model,
            "start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat(),
        }).json()
        for sid, res in zip(b.station_id, r if isinstance(r, list) else [r]):
            vals = [(v or 0.0) / CM_PER_IN for v in res["daily"]["snowfall_sum"]]
            for h, cum in zip((24, 48, 72), np.cumsum(vals)):
                rows.append({"station_id": sid, "horizon_h": h, "model": model, "inches": cum})
        time.sleep(3)  # stay well inside Open-Meteo's free tier
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="stations")
    ap.add_argument("--out", default="my-forecast.csv")
    args = ap.parse_args()

    m = open_round(args.league)
    ids = m.get("featured") or m["stations"]
    st = stations(args.league, ids)
    start = date.fromisoformat(m["target_days"][0])
    ens = pd.concat([model_daily(st, mdl, start) for mdl in MODELS], ignore_index=True)

    out = []
    for (sid, h), g in ens.groupby(["station_id", "horizon_h"]):
        x = g["inches"].to_numpy()
        q = np.percentile(x, [10, 25, 50, 75, 90])
        row = {"station_id": sid, "horizon_h": int(h), "snowfall_in": round(float(q[2]), 2),
               **{f"p{p}": round(float(v), 2) for p, v in zip((10, 25, 50, 75, 90), q)}}
        if h == 24:
            row["prob_6in"] = round(float((np.sum(x >= 6) + 0.1) / (len(x) + 1)), 3)
        out.append(row)
    pd.DataFrame(out).to_csv(args.out, index=False)
    print(f"round {args.league}/{m['round_id']} (cutoff {m['cutoff_utc']}): {len(out)} rows -> {args.out}")
    print(f"next: powderbench submit {args.out} --team <your-team> --league {args.league}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

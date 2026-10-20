"""Generate data/stations/slf.csv for the `alps` league: SLF manual study
plots (observer-measured HN) and automatic IMIS snow stations (HN_1D derived
by SLF from snow depth). CC BY 4.0 — WSL Institute for Snow and Avalanche
Research SLF, www.slf.ch.

Selection: active, >= MIN_REPORTS HN_1D readings last winter, and a clear
observation-day fit against ERA5 (powderbench/networks/attribution.py).
Featured: the study plots at the famous resorts (FEATURED).

    python scripts/networks/slf.py
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "data" / "stations" / "slf.csv"
STUDY_PLOTS = "https://measurement-data.slf.ch/study-plot/stations.csv"
IMIS = "https://measurement-api.slf.ch/public/api/imis/stations"
MIN_REPORTS = 100
FEATURED = {  # study plot -> resort
    "4ZE0": "Zermatt", "4SF0": "Saas-Fee", "4RU0": "Verbier", "4MO0": "Crans-Montana",
    "4GR0": "Grimentz", "4LA0": "Lauchernalp", "7MZ0": "St. Moritz", "7CO0": "Corvatsch",
    "5WJ0": "Davos Parsenn", "5KK0": "Klosters", "5AR0": "Arosa", "5PL0": "Laax",
    "2TR0": "Engelberg-Titlis", "2AN0": "Andermatt", "1GB0": "Grindelwald", "1MR0": "Mürren",
    "1WE0": "Wengen", "1AD0": "Adelboden", "7SN0": "Samnaun",
}
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "network", "external_id", "day_offset"]
CANTON_REGION = {
    "VS": "Valais", "GR": "Graubünden", "BE": "Bernese Oberland", "UR": "Central Switzerland",
    "OW": "Central Switzerland", "NW": "Central Switzerland", "SZ": "Central Switzerland",
    "LU": "Central Switzerland", "GL": "Eastern Switzerland", "SG": "Eastern Switzerland",
    "AR": "Eastern Switzerland", "AI": "Eastern Switzerland", "TI": "Ticino", "VD": "Vaud & Fribourg Alps",
    "FR": "Vaud & Fribourg Alps", "LI": "Eastern Switzerland",
}
CODE_REGION = {"1": "Bernese Oberland", "2": "Central Switzerland", "3": "Eastern Switzerland", "4": "Valais",
               "5": "Graubünden", "6": "Ticino", "7": "Graubünden"}


def main() -> int:
    import pandas as pd

    from powderbench.networks import attribution, slf
    from powderbench.openmeteo import era5_daily_snowfall
    from powderbench.stations import Station

    rows = []
    plots = pd.read_csv(io.StringIO(requests.get(STUDY_PLOTS, timeout=120).text))
    for p in plots[plots["active"]].itertuples():
        c = p.station_code
        rows.append({"station_id": f"{c}:CH:SLF", "slug": f"slf-{c.lower()}", "name": p.label,
                     "resort": FEATURED.get(c, p.label), "region": CODE_REGION.get(c[0], "Swiss Alps"),
                     "state": "CH", "elevation_ft": round(p.elevation * 3.28084), "latitude": round(p.lat, 5),
                     "longitude": round(p.lon, 5), "league": "alps",
                     "tier": "featured" if c in FEATURED else "network", "network": "SLF study plot",
                     "external_id": c})
    for s in requests.get(IMIS, timeout=120).json():
        if s.get("type") != "SNOW_FLAT" or s.get("country_code") != "CH":
            continue
        c = s["code"]
        rows.append({"station_id": f"{c}:CH:IMIS", "slug": f"imis-{c.lower()}", "name": s["label"],
                     "resort": s["label"], "region": CANTON_REGION.get(s.get("canton_code"), "Swiss Alps"),
                     "state": "CH", "elevation_ft": round(s["elevation"] * 3.28084), "latitude": round(s["lat"], 5),
                     "longitude": round(s["lon"], 5), "league": "alps", "tier": "network",
                     "network": "SLF IMIS", "external_id": c})

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    sts = [Station(**{k: r[k] for k in ("station_id", "slug", "name", "resort", "region", "state",
                                        "elevation_ft", "latitude", "longitude")}) for r in rows]
    reps, mods = [], []
    for b, e in attribution.winters(last):
        reps.append(slf.fetch_snow([s.station_id for s in sts], b, e).dropna())
        mods.append(era5_daily_snowfall(sts, b, e))
    reports = pd.concat(reps)
    reports = reports[reports["snow_in"] >= 0]
    n_last = reports[reports["date"] >= attribution.winters(last)[-1][0]].groupby("station_id").size()
    # study plots are read at 07:00 local by protocol (measure_date 06:00 UTC):
    # the board holds the previous day's snow, so the fit only has to confirm +1
    documented = {r["station_id"]: 1 for r in rows if r["station_id"].endswith(":SLF")}
    fits = attribution.fit_offsets(reports, pd.concat(mods), allowed=(0, 1), expected=documented)

    kept, counts = [], {}
    for r in rows:
        off = fits.get(r["station_id"], {}).get("offset")
        n = int(n_last.get(r["station_id"], 0))
        ok = n >= MIN_REPORTS and off is not None
        counts[(r["network"], off if ok else None)] = counts.get((r["network"], off if ok else None), 0) + 1
        if r["tier"] == "featured":
            print(f"{r['station_id']:>14} {r['resort']:<18} n={n:<4} corr={fits.get(r['station_id'], {}).get('corr')} -> {'ok' if ok else 'DROPPED'}")
        if ok:
            r["day_offset"] = off
            kept.append(r)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: (r["network"], r["name"])))
    print(counts)
    print(f"{len(rows)} candidates -> {len(kept)} kept ({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

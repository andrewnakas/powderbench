"""Generate data/stations/geosphere.csv: GeoSphere Austria climate stations
with hand-measured new snow (shneu_manu), for the alps league.

Candidates: active stations at >= MIN_ALT_M with a manual new-snow record in
the last winter (>= MIN_REPORTS readings). GeoSphere lists many sites twice
(an old and a new station number, identical values): one is kept per site,
the one with the longer record. Day attribution is fitted per station against
ERA5: the 07:00 protocol (+1) unless 0 wins clearly (see the adapter).

    python scripts/networks/geosphere.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "geosphere.csv"
META = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-1d/metadata"
MIN_ALT_M = 900
MIN_REPORTS = 100
ALPS_MIN_LIFTS = 4
FEATURED = {  # station name -> resort
    "Galzig": "St. Anton (Galzig)", "St. Anton am Arlberg": "St. Anton am Arlberg", "Warth": "Warth-Schröcken",
    "Langen am Arlberg": "Lech / Zürs (Langen)", "Obergurgl": "Obergurgl-Hochgurgl", "Obertauern": "Obertauern",
    "Galtür": "Galtür / Ischgl", "Saalbach": "Saalbach Hinterglemm", "Seefeld": "Seefeld",
    "Pitztaler Gletscher": "Pitztal Glacier", "Nauders": "Nauders",
}
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def _norm(name: str) -> str:
    return name.replace("St.", "St. ").replace("  ", " ").strip()


def main() -> int:
    import pandas as pd

    from powderbench.networks import attribution, geosphere
    from powderbench.openmeteo import era5_daily_snowfall
    from powderbench.stations import Station

    meta = requests.get(META, timeout=120).json()["stations"]
    cands = [s for s in meta if s.get("is_active") and (s.get("altitude") or 0) >= MIN_ALT_M]
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = attribution.winters(last)[-1]
    reports = geosphere.fetch_snow([f"{s['id']}:AT:GEO" for s in cands], b, e).dropna()
    n_last = reports.groupby("station_id").size()

    by_site: dict[tuple, dict] = {}
    for s in cands:
        sid = f"{s['id']}:AT:GEO"
        if n_last.get(sid, 0) < MIN_REPORTS:
            continue
        key = (_norm(s["name"]), round(s["lat"], 3), round(s["lon"], 3))
        if key not in by_site or s["valid_from"] < by_site[key]["valid_from"]:
            by_site[key] = s
    rows = []
    for s in by_site.values():
        name = _norm(s["name"])
        rows.append({"station_id": f"{s['id']}:AT:GEO", "slug": f"geo-{s['id']}", "name": name,
                     "resort": FEATURED.get(name) or skiareas.label(name, s["lat"], s["lon"], min_lifts=ALPS_MIN_LIFTS),
                     "region": s["state"], "state": "AT", "elevation_ft": round(s["altitude"] * 3.28084),
                     "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                     "tier": "featured" if name in FEATURED else "network", "truth_source": "geosphere",
                     "network": "GeoSphere Austria", "external_id": str(s["id"])})

    sts = [Station(**{k: r[k] for k in ("station_id", "slug", "name", "resort", "region", "state",
                                        "elevation_ft", "latitude", "longitude")}) for r in rows]
    reps, mods = [], []
    for wb, we in attribution.winters(last):
        reps.append(geosphere.fetch_snow([s.station_id for s in sts], wb, we).dropna())
        mods.append(era5_daily_snowfall(sts, wb, we))
    # the documented 06 UTC (07:00) reading covers mostly yesterday (+1): a
    # near-tie takes the protocol, a clear win for 0 (snow that mostly falls
    # overnight, e.g. St. Anton) overrides it, and weak tracking drops the site
    fits = attribution.fit_offsets(pd.concat(reps), pd.concat(mods), allowed=(0, 1),
                                   expected={r["station_id"]: 1 for r in rows})
    kept = []
    for r in rows:
        fit = fits.get(r["station_id"], {})
        off = fit.get("offset")
        print(f"{r['station_id']:>12} {r['name']:<28} {r['tier']:<8} corr={fit.get('corr')} -> {off}")
        if off is not None:
            r["day_offset"] = off
            kept.append(r)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: r["name"]))
    print(f"{len(cands)} candidates -> {len(rows)} sites -> {len(kept)} kept "
          f"({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

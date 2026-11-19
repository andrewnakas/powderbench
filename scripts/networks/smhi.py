"""Generate data/stations/smhi.csv: SMHI stations with a daily snow-depth
reading, for the nordic league.

Candidates: active parameter-8 stations in snow country (within NEAR_KM of a
ski area, or at >= MIN_ALT_M). Kept when last winter (Nov-Mar) has
>= MIN_DAYS valid days and >= MIN_SNOW_DAYS days with >= 2 cm of new snow.
Stations near a ski area with >= FEATURED_LIFTS lifts are featured.

    python scripts/networks/smhi.py
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

OUT = ROOT / "data" / "stations" / "smhi.csv"
META = "https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/8.json"
NEAR_KM = 25.0
MIN_ALT_M = 400
FEATURED_KM = 12.0
FEATURED_LIFTS = 3
MIN_DAYS = 120
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def main() -> int:
    from powderbench.networks import smhi

    meta = requests.get(META, headers=smhi.UA, timeout=120).json()["station"]
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    kept = 0
    rows = []
    for s in meta:
        if not s.get("active"):
            continue
        lat, lon, alt = s["latitude"], s["longitude"], s.get("height") or 0
        area, d = skiareas.nearest(lat, lon, 1)
        if alt < MIN_ALT_M and (area is None or d > NEAR_KM):
            continue
        sid = f"{s['id']}:SE:SMHI"
        t = smhi.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS
        big, bd = skiareas.nearest(lat, lon, FEATURED_LIFTS)
        featured = big is not None and bd <= FEATURED_KM
        print(f"{sid:>16} {s['name']:<28} {alt:6.0f} m days={ok_days} snow_days={snow_days} "
              f"{'featured' if featured else 'network'} -> {'keep' if keep else 'drop'}")
        if not keep:
            continue
        kept += 1
        rows.append({"station_id": sid, "slug": f"smhi-{s['id']}", "name": s["name"],
                     "resort": skiareas.label(s["name"], lat, lon, within_km=FEATURED_KM, min_lifts=FEATURED_LIFTS),
                     "region": "Sweden", "state": "SE", "elevation_ft": round(alt * 3.28084),
                     "latitude": round(lat, 5), "longitude": round(lon, 5), "league": "nordic",
                     "tier": "featured" if featured else "network", "truth_source": "smhi",
                     "network": "SMHI", "external_id": str(s["id"]), "day_offset": 1})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{kept} kept ({sum(r['tier'] == 'featured' for r in rows)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

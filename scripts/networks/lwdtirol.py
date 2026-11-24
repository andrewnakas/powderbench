"""Generate data/stations/lwdtirol.csv: Land Tirol's own automatic snow-depth
stations (avalanche warning service LWD Tirol, hydrographic service HD Tirol),
for the alps league.

Candidates: stations in the Euregio station list (lawinen.report's
`linea.geojson.gz`) in Tirol (micro-regions AT-07-*) operated by LWD or HD
Tirol, the operators covered by the "Wetterstationsdaten Tirol" open-data
licence (CC BY 4.0). Stations run by third parties (power companies, lift
operators, universities) are left out: their data is not under that licence.

Kept when last winter (Dec-Mar) has >= MIN_DAYS midnight-to-midnight depth
rises and >= MIN_SNOW_DAYS days of >= 2 cm. Featured: for each ski area with
>= FEATURED_LIFTS lifts (OpenSkiData), the nearest kept station within
FEATURED_KM (one per area, so Sölden's four stations cost one Open-Meteo point).

    python scripts/networks/lwdtirol.py
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "lwdtirol.csv"
LIST = "https://static.avalanche.report/weather_stations/linea.geojson.gz"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
OPERATORS = ("LWD Tirol", "HD Tirol")  # incl. joint stations such as "LWD Tirol/ÖBB"
FEATURED_KM = 4.0
FEATURED_LIFTS = 15
DUP_KM = 0.3
MIN_DAYS = 100
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def candidates() -> list[dict]:
    body = requests.get(LIST, headers=UA, timeout=120).content
    feats = json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)["features"]
    out = []
    for f in feats:
        p = f["properties"]
        op = p.get("operator") or ""
        if not str(p.get("microRegionID", "")).startswith("AT-07") or not op.startswith(OPERATORS):
            continue
        if "HS" not in json.dumps(p) and p.get("shortName") is None:
            continue
        lon, lat, *alt = f["geometry"]["coordinates"]
        out.append({"id": p.get("shortName") or p.get("LWD-Nummer"), "name": p["name"], "operator": op,
                    "lat": lat, "lon": lon, "alt": alt[0] if alt else None})
    return [c for c in out if c["id"]]


def main() -> int:
    from powderbench.networks import lwdtirol

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 12, 1), date(last, 3, 31)
    rows = []
    nearest: dict[str, tuple[float, str]] = {}  # ski area -> (km, station) of its closest station
    for s in candidates():
        sid = f"{s['id']}:AT:LWDT"
        t = lwdtirol.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and s["alt"] is not None
        big, bd = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        print(f"{sid:>14} {s['name']:<34} {s['alt'] or 0:6.0f} m {s['operator']:<12} days={ok_days} "
              f"snow_days={snow_days} -> {'keep' if keep else 'drop'}")
        if keep:
            rows.append({"station_id": sid, "slug": f"lwdt-{s['id'].lower()}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "Tirol", "state": "AT", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                         "tier": "network", "truth_source": "lwdtirol",
                         "network": s["operator"], "external_id": s["id"], "day_offset": 0, "_days": ok_days,
                         "_area": big["name"] if big is not None and bd <= FEATURED_KM else None, "_km": bd})
    # one sensor per site: where LWD and HD both run a station (< DUP_KM apart),
    # keep the LWD one, else the one with more valid days
    rows.sort(key=lambda r: (not r["network"].startswith("LWD"), -r["_days"]))
    kept: list[dict] = []
    for r in rows:
        if all(skiareas.km(r["latitude"], r["longitude"], k["latitude"], k["longitude"]) >= DUP_KM for k in kept):
            kept.append(r)
        else:
            print(f"  duplicate site, dropped {r['station_id']} {r['name']}")
    rows = kept
    for r in rows:
        if r["_area"] and (r["_area"] not in nearest or r["_km"] < nearest[r["_area"]][0]):
            nearest[r["_area"]] = (r["_km"], r["station_id"])
    chosen = {sid: area for area, (_, sid) in nearest.items()}
    for r in rows:
        if r["station_id"] in chosen:
            r["tier"] = "featured"
            r["resort"] = chosen[r["station_id"]]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"-> {len(rows)} kept ({sum(r['tier'] == 'featured' for r in rows)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

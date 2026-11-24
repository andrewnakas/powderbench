"""Generate data/stations/arpal.csv: ARPA Lombardia's automatic snow-depth
sensors, for the alps league.

Candidates: the active "Altezza Neve" sensors in ARPA Lombardia's station
register (dati.lombardia.it nf78-nj6b, CC0 1.0).

Kept when either of the last two winters' Dec-Feb has >= MIN_DAYS
midnight-to-midnight depth rises and >= MIN_SNOW_DAYS days of >= 2 cm
(Dec-Feb because the validated archive lags months behind and the live
set starts in spring; two winters so sensors installed in autumn 2025 can
qualify). Featured: for each ski area with >= FEATURED_LIFTS lifts
(OpenSkiData), the nearest kept station within FEATURED_KM.

    python scripts/networks/arpal.py
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

OUT = ROOT / "data" / "stations" / "arpal.csv"
REGISTER = "https://www.dati.lombardia.it/resource/nf78-nj6b.json"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
FEATURED_KM = 4.0
FEATURED_LIFTS = 10
DUP_KM = 0.3
MIN_DAYS = 75  # of 90
MIN_SNOW_DAYS = 8
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def candidates() -> list[dict]:
    params = {"$where": "tipologia='Altezza Neve' AND datastop IS NULL", "$limit": 1000}
    rows = requests.get(REGISTER, params=params, headers=UA, timeout=120).json()
    return [{"id": r["idsensore"], "name": r["nomestazione"], "prov": r.get("provincia"),
             "lat": float(r["lat"]), "lon": float(r["lng"]), "alt": float(r["quota"])} for r in rows]


def main() -> int:
    from powderbench.networks import arpal

    today = date.today()
    last = today.year if today.month >= 3 else today.year - 1
    winters = [(date(y - 1, 12, 1), date(y, 2, 28)) for y in (last - 1, last)]
    rows = []
    for s in candidates():
        sid = f"{s['id']}:IT:ARPAL"
        best = (0, 0)
        for b, e in winters:
            t = arpal.daily([sid], b, e)
            best = max(best, (int(t["valid"].sum()), int((t["snow24"] >= 2 / 2.54).sum())))
        ok_days, snow_days = best
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS
        big, bd = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        area = big["name"] if big is not None and bd <= FEATURED_KM else None
        print(f"{sid:>14} {s['name']:<40} {s['alt']:6.0f} m days={ok_days} snow_days={snow_days} "
              f"near={area or '-'} -> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            rows.append({"station_id": sid, "slug": f"arpal-{s['id']}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "Lombardia", "state": "IT", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                         "tier": "network", "truth_source": "arpal", "network": "ARPA Lombardia",
                         "external_id": s["id"], "day_offset": 0, "_days": ok_days, "_area": area, "_km": bd})
    rows.sort(key=lambda r: -r["_days"])
    kept: list[dict] = []
    for r in rows:
        if all(skiareas.km(r["latitude"], r["longitude"], k["latitude"], k["longitude"]) >= DUP_KM for k in kept):
            kept.append(r)
        else:
            print(f"  duplicate site, dropped {r['station_id']} {r['name']}")
    nearest: dict[str, tuple[float, str]] = {}
    for r in kept:
        if r["_area"] and (r["_area"] not in nearest or r["_km"] < nearest[r["_area"]][0]):
            nearest[r["_area"]] = (r["_km"], r["station_id"])
    chosen = {sid: area for area, (_, sid) in nearest.items()}
    for r in kept:
        if r["station_id"] in chosen:
            r["tier"] = "featured"
            r["resort"] = chosen[r["station_id"]]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: r["name"]))
    print(f"-> {len(kept)} kept ({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

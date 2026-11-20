"""Generate data/stations/provbz.csv: the snow-depth (HS) stations of the
South Tyrol provincial weather service, for the alps league.

Candidates: every station carrying an HS sensor in the province's open
meteo API (data.civis.bz.it "Misure meteo e idrografiche", CC0 1.0).

Kept when last winter (Dec-Mar) has >= MIN_DAYS midnight-to-midnight depth
rises and >= MIN_SNOW_DAYS days of >= 2 cm. Featured: for each ski area with
>= FEATURED_LIFTS lifts (OpenSkiData), the nearest kept station within
FEATURED_KM (one per area, as for Tirol and Trentino).

    python scripts/networks/provbz.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "provbz.csv"
FEATURED_KM = 4.0
FEATURED_LIFTS = 10
MIN_DAYS = 100
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def candidates() -> list[dict]:
    from powderbench.networks import provbz

    hs = {s["SCODE"] for s in provbz._get("sensors", sensor_code="HS")}
    out = []
    for f in provbz._get("stations")["features"]:
        p = f["properties"]
        if p["SCODE"] in hs:
            out.append({"id": p["SCODE"], "name": p["NAME_D"], "name_i": p["NAME_I"],
                        "lat": p["LAT"], "lon": p["LONG"], "alt": p.get("ALT")})
    return out


def main() -> int:
    from powderbench.networks import provbz

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 12, 1), date(last, 3, 31)
    rows = []
    for s in candidates():
        sid = f"{s['id']}:IT:PROVBZ"
        t = provbz.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and s["alt"] is not None
        big, bd = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        area = big["name"] if big is not None and bd <= FEATURED_KM else None
        print(f"{sid:>18} {s['name']:<30} {s['alt'] or 0:6.0f} m days={ok_days} snow_days={snow_days} "
              f"total={t['snow24'].sum():5.1f} in near={area or '-'} -> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            name = s["name"].title() if s["name"].isupper() else s["name"]
            rows.append({"station_id": sid, "slug": f"provbz-{s['id'].lower()}", "name": name,
                         "resort": skiareas.label(name, s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "South Tyrol", "state": "IT", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                         "tier": "network", "truth_source": "provbz", "network": "Provincia di Bolzano",
                         "external_id": s["id"], "day_offset": 0, "_area": area, "_km": bd})
    nearest: dict[str, tuple[float, str]] = {}
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

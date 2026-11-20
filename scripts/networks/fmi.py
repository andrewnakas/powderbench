"""Generate data/stations/fmi.csv: FMI automatic weather stations with a daily
snow-depth reading, for the nordic league.

Candidates: active automatic weather stations (FMI network 121) within
NEAR_KM of a ski area or north of MIN_LAT (Lapland). FMI's station list has no
elevation, so it comes from the Copernicus DEM via Open-Meteo's elevation API
(two calls). Kept on the same last-winter gate as SMHI.

    python scripts/networks/fmi.py
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "fmi.csv"
STATIONS = ("https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature"
            "&storedquery_id=fmi::ef::stations&networkid=121")
ELEV = "https://api.open-meteo.com/v1/elevation"
NEAR_KM = 25.0
MIN_LAT = 66.0
FEATURED_KM = 12.0
FEATURED_LIFTS = 3
MIN_DAYS = 120
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def stations() -> list[dict]:
    from powderbench.networks import fmi

    x = requests.get(STATIONS, headers=fmi.UA, timeout=120).text
    out = []
    for f in re.findall(r"<ef:EnvironmentalMonitoringFacility.*?</ef:EnvironmentalMonitoringFacility>", x, re.S):
        if 'xlink:title="Automaattinen sääasema"' not in f:
            continue
        fid = re.search(r"<gml:identifier[^>]*>([^<]*)", f).group(1)
        name = re.search(r"<gml:name[^>]*>([^<]*)", f).group(1)
        lat, lon = map(float, re.search(r"<gml:pos>([^<]*)", f).group(1).split())
        out.append({"id": fid, "name": name, "lat": lat, "lon": lon})
    return out


def elevations(sts: list[dict]) -> None:
    for i in range(0, len(sts), 100):
        part = sts[i : i + 100]
        r = requests.get(ELEV, params={"latitude": ",".join(f"{s['lat']}" for s in part),
                                       "longitude": ",".join(f"{s['lon']}" for s in part)}, timeout=60)
        for s, e in zip(part, r.json()["elevation"]):
            s["alt"] = float(e)


def main() -> int:
    from powderbench.networks import fmi

    cands = []
    for s in stations():
        area, d = skiareas.nearest(s["lat"], s["lon"], 1)
        if s["lat"] >= MIN_LAT or (area is not None and d <= NEAR_KM):
            cands.append(s)
    elevations(cands)
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    rows = []
    for s in cands:
        sid = f"{s['id']}:FI:FMI"
        t = fmi.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS
        offset, why = gate.decide("nordic", sid, 1) if keep else (None, "")
        keep = keep and offset is not None
        big, bd = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        featured = big is not None and bd <= FEATURED_KM
        print(f"{sid:>14} {s['name']:<30} {s['alt']:6.0f} m days={ok_days} snow_days={snow_days} "
              f"{'featured' if featured else 'network'} {why} -> {'keep' if keep else 'drop'}")
        if keep:
            rows.append({"station_id": sid, "slug": f"fmi-{s['id']}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "Finland", "state": "FI", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "nordic",
                         "tier": "featured" if featured else "network", "truth_source": "fmi",
                         "network": "FMI", "external_id": s["id"], "day_offset": offset})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(cands)} candidates -> {len(rows)} kept "
          f"({sum(r['tier'] == 'featured' for r in rows)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

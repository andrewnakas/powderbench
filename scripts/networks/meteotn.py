"""Generate data/stations/meteotn.csv: Meteotrentino manual snow fields
(AINEVA Modello 1, measured new snow HN) for the alps league.

Candidates: every field in Meteotrentino's snow-field list (`listaCampiNeve`,
UTM 32N coordinates). Kept when last winter (Dec-Mar) has >= MIN_DAYS
single-day HN values and >= MIN_SNOW_DAYS days of >= 2 cm. Fields near a big
ski area (OpenSkiData, >= FEATURED_LIFTS lifts within FEATURED_KM) are featured.

    python scripts/networks/meteotn.py
"""

from __future__ import annotations

import csv
import math
import re
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "meteotn.csv"
LIST = "https://dati.meteotrentino.it/service.asmx/listaCampiNeve"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
FEATURED_KM = 4.0
FEATURED_LIFTS = 10
MIN_DAYS = 80  # of 121 Dec-Mar days: manual fields skip some days
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]
_FIELD = re.compile(r"<codice>([^<]*)</codice>\s*<nome>([^<]*)</nome>\s*<nomebreve>[^<]*</nomebreve>\s*"
                    r"<quota>([^<]*)</quota>\s*<utmlat>([^<]*)</utmlat>\s*<utmlon>([^<]*)</utmlon>")


def utm_to_latlon(easting: float, northing: float, zone: int = 32) -> tuple[float, float]:
    """WGS84 UTM (northern hemisphere) -> lat, lon in degrees."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x, y = easting - 500000.0, northing
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
         + (151 * e1**3 / 96) * math.sin(6 * mu) + (1097 * e1**4 / 512) * math.sin(8 * mu))
    c1, t1 = ep2 * math.cos(p) ** 2, math.tan(p) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(p) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = p - (n1 * math.tan(p) / r1) * (d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
                                         + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d**3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / math.cos(p)
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lon)


def nice(name: str) -> str:
    out = name.title().replace("'S", "'s").replace(" Aut", "")
    return re.sub(r"\bDi\b", "di", out)


def fields() -> list[dict]:
    txt = requests.get(LIST, headers=UA, timeout=120).text
    out = []
    for code, name, alt, east, north in _FIELD.findall(txt):
        lat, lon = utm_to_latlon(float(east), float(north))
        out.append({"code": code.strip(), "name": nice(name.strip()), "alt": float(alt), "lat": lat, "lon": lon})
    return out


def main() -> int:
    from powderbench.networks import meteotn

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 12, 1), date(last, 3, 31)
    rows = []
    for s in fields():
        sid = f"{s['code']}:IT:METEOTN"
        t = meteotn.daily({sid: s["code"]}, b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS
        big, bd = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        featured = big is not None and bd <= FEATURED_KM
        print(f"{sid:>16} {s['name']:<34} {s['alt']:6.0f} m days={ok_days} snow_days={snow_days} "
              f"{'featured' if featured else 'network'} -> {'keep' if keep else 'drop'}")
        if keep:
            rows.append({"station_id": sid, "slug": f"meteotn-{s['code'].lower()}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "Trentino", "state": "IT", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                         "tier": "featured" if featured else "network", "truth_source": "meteotn",
                         "network": "Meteotrentino", "external_id": s["code"],
                         "day_offset": meteotn.DEFAULT_OFFSET})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"-> {len(rows)} kept ({sum(r['tier'] == 'featured' for r in rows)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

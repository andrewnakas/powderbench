"""Generate data/stations/snotel.csv: every active SNOTEL station that reports
both snow depth (SNWD) and SWE (WTEQ) — the two elements the truth QC needs.

Curated stations in data/stations.yaml keep their own entries (and stay the
featured set); this file adds the rest of the network as tier=network.

Region: the curated region of the nearest curated station within
REGION_RADIUS_KM (so a station next to Alta lands in "Wasatch, UT"), else the
state's mountains ("Montana"). Regions drive the regional leaderboard tracks.

    python scripts/networks/snotel.py
"""

from __future__ import annotations

import csv
import math
import sys
from datetime import date, datetime
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402
OUT = ROOT / "data" / "stations" / "snotel.csv"
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations"
REGION_RADIUS_KM = 60
MIN_HISTORY_YEARS = 3  # climatology needs >= 30 samples per doy window

STATE_NAMES = {
    "AK": "Alaska", "AZ": "Arizona", "CA": "California", "CO": "Colorado", "ID": "Idaho",
    "MT": "Montana", "NM": "New Mexico", "NV": "Nevada", "OR": "Oregon", "SD": "South Dakota",
    "UT": "Utah", "WA": "Washington", "WY": "Wyoming",
}
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft",
          "latitude", "longitude", "league", "network", "external_id"]


def km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def element_since(station: dict, code: str) -> date | None:
    for el in station.get("stationElements") or []:
        if el.get("elementCode") == code and el.get("durationName") == "DAILY":
            return datetime.strptime(el["beginDate"][:10], "%Y-%m-%d").date()
    return None


def main() -> int:
    curated = [s for s in yaml.safe_load((ROOT / "data" / "stations.yaml").read_text())["stations"]
               if s.get("league", "stations") == "stations"]
    stations = []
    for state in STATE_NAMES:  # all-states + elements in one call is a 400
        resp = requests.get(AWDB, params={"stationTriplets": f"*:{state}:SNTL", "activeOnly": "true",
                                          "returnStationElements": "true", "elements": "SNWD,WTEQ"},
                            timeout=180)
        resp.raise_for_status()
        stations += resp.json()

    cutoff = date.today().replace(year=date.today().year - MIN_HISTORY_YEARS)
    rows, skipped = [], {"no_snwd": 0, "no_wteq": 0, "young": 0}
    for st in stations:
        snwd, wteq = element_since(st, "SNWD"), element_since(st, "WTEQ")
        if snwd is None:
            skipped["no_snwd"] += 1
            continue
        if wteq is None:
            skipped["no_wteq"] += 1
            continue
        if snwd > cutoff:
            skipped["young"] += 1
            continue
        lat, lon = st["latitude"], st["longitude"]
        near = min(curated, key=lambda c: km(lat, lon, c["latitude"], c["longitude"]), default=None)
        if near and km(lat, lon, near["latitude"], near["longitude"]) <= REGION_RADIUS_KM:
            region = near["region"]
        else:
            region = STATE_NAMES.get(st["stateCode"], st["stateCode"])
        rows.append({
            "station_id": st["stationTriplet"],
            "slug": f"sntl-{st['stationId']}-{st['stateCode'].lower()}",
            "name": st["name"],
            "resort": skiareas.label(st["name"], lat, lon),
            "region": region,
            "state": st["stateCode"],
            "elevation_ft": round(st["elevation"]),
            "latitude": round(lat, 5),
            "longitude": round(lon, 5),
            "league": "stations",
            "network": "SNTL",
            "external_id": st.get("shefId") or "",
        })
    rows.sort(key=lambda r: (r["state"], r["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(stations)} active SNOTEL -> {len(rows)} usable ({skipped}) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

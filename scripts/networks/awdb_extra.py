"""Generate the non-SNOTEL AWDB station files for the stations league:

  data/stations/snolite.csv  NRCS SNOLITE (depth only; truth_source snolite —
                             big jumps confirmed by neighbouring SNOTEL SWE).
                             Needs a SNOTEL within 40 km and >= 3 years of depth.
  data/stations/bc_asws.csv  British Columbia automated snow pillows served by
                             AWDB as network MSNT (depth + SWE: regular SNOTEL QC).

    python scripts/networks/awdb_extra.py
"""

from __future__ import annotations

import csv
import math
import sys
import time
from datetime import date
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations"
MIN_HISTORY_YEARS = 3
NEIGHBOR_KM = 40
REGION_KM = 60
DUPLICATE_KM = 1.5  # closer than this to a SNOTEL = the same site
STATE_NAMES = {"AK": "Alaska", "CA": "California", "CO": "Colorado", "ID": "Idaho", "MT": "Montana",
               "NV": "Nevada", "OR": "Oregon", "UT": "Utah", "WA": "Washington", "WY": "Wyoming",
               "BC": "British Columbia", "AB": "Alberta"}
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "truth_source", "network", "external_id"]


def km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def stations(net: str, elements: str) -> list[dict]:
    for _ in range(6):
        r = requests.get(AWDB, params={"stationTriplets": f"*:*:{net}", "activeOnly": "true",
                                       "returnStationElements": "true", "elements": elements}, timeout=180)
        if r.ok and r.text.strip().startswith("["):
            return r.json()
        time.sleep(10)
    raise SystemExit(f"AWDB {net} unavailable")


def since(st: dict, code: str) -> str | None:
    ds = [e["beginDate"][:10] for e in st.get("stationElements") or []
          if e.get("elementCode") == code and e.get("durationName") == "DAILY"]
    return min(ds) if ds else None


def main() -> int:
    cutoff = date.today().replace(year=date.today().year - MIN_HISTORY_YEARS).isoformat()
    curated = [s for s in yaml.safe_load((ROOT / "data/stations.yaml").read_text())["stations"]
               if s.get("league", "stations") == "stations"]
    snotel = list(csv.DictReader((ROOT / "data/stations/snotel.csv").open()))

    def region(lat, lon, state):
        near = min(curated, key=lambda c: km(lat, lon, c["latitude"], c["longitude"]))
        return near["region"] if km(lat, lon, near["latitude"], near["longitude"]) <= REGION_KM else STATE_NAMES.get(state, state)

    def row(st, net, truth_source):
        lat, lon = st["latitude"], st["longitude"]
        return {"station_id": st["stationTriplet"], "slug": f"{net.lower()}-{st['stationId'].lower()}-{st['stateCode'].lower()}",
                "name": st["name"], "resort": skiareas.label(st["name"], lat, lon), "region": region(lat, lon, st["stateCode"]),
                "state": st["stateCode"], "elevation_ft": round(st["elevation"]), "latitude": round(lat, 5),
                "longitude": round(lon, 5), "league": "stations", "truth_source": truth_source,
                "network": {"SNTLT": "SNOLITE", "MSNT": "BC ASWS"}[net], "external_id": st.get("shefId") or ""}

    lite = []
    for st in stations("SNTLT", "SNWD"):
        s0 = since(st, "SNWD")
        near = min(km(st["latitude"], st["longitude"], float(t["latitude"]), float(t["longitude"])) for t in snotel)
        if s0 and s0 <= cutoff and DUPLICATE_KM < near <= NEIGHBOR_KM:
            lite.append(row(st, "SNTLT", "snolite"))
    bc = []
    for st in stations("MSNT", "SNWD,WTEQ"):
        a, b = since(st, "SNWD"), since(st, "WTEQ")
        if a and b and max(a, b) <= cutoff:
            bc.append(row(st, "MSNT", ""))
    for name, rows in (("snolite", lite), ("bc_asws", bc)):
        with (ROOT / f"data/stations/{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: (r["state"], r["name"])))
        print(f"{name}: {len(rows)} stations")
    return 0


if __name__ == "__main__":
    sys.exit(main())

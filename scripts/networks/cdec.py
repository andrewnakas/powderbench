"""Generate data/stations/cdec.csv: California DWR snow pillows (non-NRCS —
the NRCS ones are already SNOTEL) with hourly depth (sensor 18) and SWE
(sensor 3) reporting last winter. Truth: powderbench/networks/cdec.py.

    python scripts/networks/cdec.py
"""

from __future__ import annotations

import csv
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "cdec.csv"
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "truth_source", "network", "external_id"]
MIN_DAYS = 60  # midnight depth+SWE readings in Jan-Feb of last winter
DUPLICATE_KM = 1.5  # a pillow this close to a SNOTEL site is the same station listed twice


def sensor_list() -> list[list[str]]:
    for _ in range(6):
        try:
            r = requests.get("https://cdec.water.ca.gov/reportapp/javareports", params={"name": "SnowSensors"}, timeout=180)
            rows = [[re.sub("<[^>]+>", "", c).replace("&nbsp", "").strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
                    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S)]
            return [x for x in rows if len(x) == 7 and x[0] != "ID"]
        except requests.RequestException:
            time.sleep(10)
    raise SystemExit("CDEC sensor list unavailable")


def main() -> int:
    from powderbench.networks import cdec

    curated = [s for s in yaml.safe_load((ROOT / "data/stations.yaml").read_text())["stations"]
               if s.get("league", "stations") == "stations"]
    last = date.today().year if date.today().month >= 4 else date.today().year - 1
    snotel = [(float(r["latitude"]), float(r["longitude"])) for r in csv.DictReader((ROOT / "data/stations/snotel.csv").open())]
    rows = []
    for sid, name, elev, lat, lon, _, operator in sensor_list():
        if "Natural Resources" in operator:
            continue
        if min(skiareas.km(float(lat), float(lon), a, b) for a, b in snotel) <= DUPLICATE_KM:
            print(f"{sid:5} {name[:28]:<28} duplicate of a SNOTEL site -> skip")
            continue
        st = f"{sid}:CA:CDEC"
        obs = cdec.fetch_daily([st], date(last, 1, 1), date(last, 2, 28))
        n = int((obs["SNWD"].notna() & obs["WTEQ"].notna()).sum()) if len(obs) else 0
        verdict = "ok" if n >= MIN_DAYS else "skip"
        print(f"{sid:5} {name[:28]:<28} {elev:>6} midnight depth+SWE days={n:<3} {verdict}", flush=True)
        if verdict != "ok":
            continue
        la, lo = float(lat), float(lon)
        near = min(curated, key=lambda c: skiareas.km(la, lo, c["latitude"], c["longitude"]))
        region = near["region"] if skiareas.km(la, lo, near["latitude"], near["longitude"]) <= 60 else "Sierra Nevada, CA"
        rows.append({"station_id": st, "slug": f"cdec-{sid.lower()}", "name": name.title(),
                     "resort": skiareas.label(name.title(), la, lo), "region": region, "state": "CA",
                     "elevation_ft": int(float(elev)), "latitude": la, "longitude": lo, "league": "stations",
                     "truth_source": "cdec", "network": "CDEC", "external_id": sid})
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(rows)} CDEC stations -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

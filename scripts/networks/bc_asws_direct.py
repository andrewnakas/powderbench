"""Generate data/stations/bc_asws_direct.csv: British Columbia automated snow
pillows read from BC's own feed (truth_source bcasws, ids '<BC id>:BC:ASWS'),
for the stations the league doesn't already have through NRCS AWDB
(data/stations/bc_asws.csv, network MSNT — those keep their AWDB truth).

A station is kept when it
  - is in BC's hourly feed and reported depth in the last 7 days;
  - is not an AWDB MSNT station (same BC id) and is more than 1.5 km from
    every other registry station (any league), and from a longer-record
    candidate;
  - has midnight depth + SWE on >= 50 of the 59 days of Jan-Feb last winter
    (so a record before this season, and an SWE sensor);
  - passes the hindcast gate in data/gates/stations.csv
    (scripts/hindcast_gate.py, see data/gates/README.md). A station that
    isn't in the gate file yet is held back.

Locations and elevations: DataBC layer WHSE_WATER_MANAGEMENT.SSL_SNOW_ASWS_STNS_SP.
Open Government Licence – British Columbia.

    python scripts/networks/bc_asws_direct.py              # gate applied
    python scripts/networks/bc_asws_direct.py --ungated    # every candidate (input to the gate)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "bc_asws_direct.csv"
GATE = ROOT / "data" / "gates" / "stations.csv"
WFS = ("https://openmaps.gov.bc.ca/geo/pub/WHSE_WATER_MANAGEMENT.SSL_SNOW_ASWS_STNS_SP/ows?service=WFS"
       "&version=2.0.0&request=GetFeature&typeName=WHSE_WATER_MANAGEMENT.SSL_SNOW_ASWS_STNS_SP"
       "&outputFormat=json&srsName=EPSG:4326")
# manual snow courses: elevation fallback for a pillow DataBC lists without one
MSS = WFS.replace("SSL_SNOW_ASWS_STNS_SP", "SSL_SNOW_MSS_LOCS_SP")
TZ_URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "truth_source", "network", "external_id"]
MIN_DAYS = 50  # midnight depth+SWE readings in Jan-Feb (59 days) of last winter
LIVE_DAYS = 7
DUPLICATE_KM = 1.5  # this close to another station = the same site listed twice
REGION_KM = 60


def get(url: str, **kw) -> requests.Response:
    for attempt in range(6):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))
    raise AssertionError


def locations() -> dict[str, dict]:
    out = {f["properties"]["LOCATION_ID"]: dict(f["properties"]) for f in get(WFS).json()["features"]}
    courses = {f["properties"]["LOCATION_ID"]: f["properties"] for f in get(MSS).json()["features"]}
    for c, m in out.items():
        course = courses.get(c.rstrip("P").rstrip("A"))
        if not m.get("ELEVATION") and course and course.get("ELEVATION") and skiareas.km(
                m["LATITUDE"], m["LONGITUDE"], course["LATITUDE"], course["LONGITUDE"]) <= 1.0:
            m["ELEVATION"] = course["ELEVATION"]  # the pillow sits on its snow course
    return out


def zones(meta: dict[str, dict], ids: list[str]) -> dict[str, str]:
    """IANA zone per station, by the lookup the NWP baselines use (Open-Meteo timezone=auto)."""
    out = {}
    for i in range(0, len(ids), 25):
        b = ids[i : i + 25]
        res = get(TZ_URL, params={"latitude": ",".join(str(meta[c]["LATITUDE"]) for c in b),
                                  "longitude": ",".join(str(meta[c]["LONGITUDE"]) for c in b),
                                  "daily": "snowfall_sum", "timezone": "auto", "forecast_days": 1}).json()
        for c, r in zip(b, res if isinstance(res, list) else [res]):
            out[c] = r["timezone"]
        time.sleep(3)
    return out


def gate_decisions() -> dict[str, dict]:
    if not GATE.exists():
        return {}
    with GATE.open(encoding="utf-8") as f:
        return {r["station_id"]: r for r in csv.DictReader(f)}


def main() -> int:
    from powderbench import stations as registry
    from powderbench.networks import bcasws

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ungated", action="store_true", help="write every candidate (the gate's input)")
    a = ap.parse_args()

    meta = locations()
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    recent = bcasws.hourly("SNWD", pd.Timestamp(today - timedelta(days=LIVE_DAYS)).to_pydatetime(),
                           pd.Timestamp(today + timedelta(days=1)).to_pydatetime())
    live = [c for c in recent.columns if recent[c].notna().any()]
    msnt = {r["station_id"].split(":")[0] for r in csv.DictReader((ROOT / "data/stations/bc_asws.csv").open())}
    registry.clear_cache()
    # ground stations of the North American station leagues (not resort report points or ERA5 cells)
    others = [s for s in registry.by_id().values()
              if s.league in ("stations", "observers") and s.truth_source != "bcasws"]
    curated = [s for s in yaml.safe_load((ROOT / "data/stations.yaml").read_text())["stations"]
               if s.get("league", "stations") == "stations"]
    gate = gate_decisions()

    cands = []
    for c in sorted(recent.columns):
        m = meta.get(c)
        if c in msnt:
            print(f"{c:7} {'':28} in AWDB (MSNT) already -> skip")
            continue
        if c not in live:
            print(f"{c:7} {(m or {}).get('LOCATION_NAME', '')[:28]:<28} no depth in the last {LIVE_DAYS} days -> skip")
            continue
        if m is None:
            print(f"{c:7} {'':28} not in DataBC's station layer -> skip")
            continue
        la, lo = float(m["LATITUDE"]), float(m["LONGITUDE"])
        near = min(others, key=lambda s: skiareas.km(la, lo, s.latitude, s.longitude))
        dist = skiareas.km(la, lo, near.latitude, near.longitude)
        if dist <= DUPLICATE_KM:
            print(f"{c:7} {m['LOCATION_NAME'][:28]:<28} {dist:.2f} km from {near.station_id} -> skip")
            continue
        cands.append(c)

    sids = [bcasws.station_id(c) for c in cands]
    obs = bcasws.fetch_daily(sids, date(last, 1, 1), date(last, 2, 28))
    both = obs[obs["SNWD"].notna() & obs["WTEQ"].notna()].groupby("station_id").size()
    hist = bcasws.fetch_daily(sids, date(last - 3, 10, 1), date(last, 5, 31))
    nhist = hist[hist["SNWD"].notna()].groupby("station_id").size()
    nswe = hist[hist["WTEQ"].notna()].groupby("station_id").size()

    tz = zones(meta, cands)
    wrong = [c for c in cands if (tz[c] in bcasws.MOUNTAIN_ZONES) != (c in bcasws.MOUNTAIN_IDS)]
    if wrong:
        raise SystemExit(f"update bcasws.MOUNTAIN_IDS: {[(c, tz[c]) for c in wrong]}")
    unknown = sorted({tz[c] for c in cands} - set(bcasws.MOUNTAIN_ZONES) - {"America/Vancouver"})
    if unknown:
        raise SystemExit(f"unexpected time zones {unknown}")

    kept, taken = [], []
    for c in sorted(cands, key=lambda c: -int(nhist.get(bcasws.station_id(c), 0))):
        m, st = meta[c], bcasws.station_id(c)
        la, lo = float(m["LATITUDE"]), float(m["LONGITUDE"])
        name = str(m["LOCATION_NAME"]).strip()
        n = int(both.get(st, 0))
        line = f"{c:7} {name[:28]:<28} {float(m['ELEVATION'] or 0):>6.0f} m  {tz[c][8:]:<12} Jan-Feb depth+SWE days={n:<3}"
        twin = next((t for t in taken if skiareas.km(la, lo, t[0], t[1]) <= DUPLICATE_KM), None)
        if twin:
            print(f"{line} {DUPLICATE_KM} km from {twin[2]} (longer record) -> skip")
            continue
        if n < MIN_DAYS:
            why = ("no depth record before this season" if not nhist.get(st) else
                   "no SWE" if not nswe.get(st) else "too few")
            print(f"{line} {why} -> skip")
            continue
        if not m.get("ELEVATION"):
            print(f"{line} no elevation in DataBC -> skip")
            continue
        g = gate.get(st)
        if not a.ungated:
            if g is None:
                print(f"{line} not gated yet -> hold")
                continue
            if g["verdict"] != "pass":
                print(f"{line} gate {g['verdict']} (r {g['r_minus1']}/{g['r_0']}/{g['r_plus1']}) -> drop")
                continue
        print(f"{line} ok" + (f" (gate r_0 {g['r_0']})" if g else ""))
        taken.append((la, lo, st))
        near = min(curated, key=lambda s: skiareas.km(la, lo, s["latitude"], s["longitude"]))
        region = near["region"] if skiareas.km(la, lo, near["latitude"], near["longitude"]) <= REGION_KM else "British Columbia"
        kept.append({"station_id": st, "slug": f"bcasws-{c.lower()}", "name": name,
                     "resort": skiareas.label(name, la, lo), "region": region, "state": "BC",
                     "elevation_ft": round(float(m["ELEVATION"]) / 0.3048), "latitude": round(la, 5),
                     "longitude": round(lo, 5), "league": "stations", "truth_source": "bcasws",
                     "network": "BC ASWS", "external_id": c})
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: r["name"]))
    print(f"{len(kept)} BC ASWS stations -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

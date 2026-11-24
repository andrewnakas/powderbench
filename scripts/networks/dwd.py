"""Generate data/stations/dwd.csv: DWD precipitation stations in the German
Alps with an observer's new-snow measurement (NSH_TAG), for the alps league.

Candidates: stations active this month inside ALPS_BOX at >= MIN_ALT_M.
With --world: the stations at >= MIN_ALT_M *outside* ALPS_BOX instead (Black
Forest, Bavarian Forest, Fichtelgebirge, Erzgebirge, Thuringian Forest, Harz,
Rhön, Sauerland, Eifel...), all network tier, for the world league
(data/stations/dwd_world.csv).
Kept when last winter (Nov-Mar) has >= MIN_DAYS new-snow values and
>= MIN_SNOW_DAYS days of >= 2 cm. Day attribution is DWD's documented morning
reading (offset 1), confirmed for every high station against the
station's own precipitation (see the adapter docstring).

    python scripts/networks/dwd.py [--world]
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "dwd.csv"
OUT_WORLD = ROOT / "data" / "stations" / "dwd_world.csv"
LIST = ("https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/more_precip/"
        "recent/RR_Tageswerte_Beschreibung_Stationen.txt")
ALPS_BOX = (47.2, 47.95, 9.8, 13.2)  # lat min/max, lon min/max: Allgäu .. Berchtesgaden
MIN_ALT_M = 600
FEATURED_KM = 6.0
FEATURED_LIFTS = 6
MIN_DAYS = 100
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def stations() -> list[dict]:
    txt = requests.get(LIST, timeout=120).content.decode("latin1").splitlines()[2:]
    cutoff = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    out = []
    for line in txt:
        f = line.split()
        if len(f) < 7 or f[2] < cutoff:
            continue
        # name may hold spaces; the Bundesland and "Frei" trail it
        name = " ".join(f[6:-2]) if len(f) > 8 else f[6]
        out.append({"id": f[0], "alt": float(f[3]), "lat": float(f[4]), "lon": float(f[5]), "name": name,
                    "state": f[-2]})
    return out


def main() -> int:
    from powderbench.networks import dwd

    ap = argparse.ArgumentParser()
    ap.add_argument("--world", action="store_true", help="the mountains outside the Alps, for the world league")
    world = ap.parse_args().world
    lat0, lat1, lon0, lon1 = ALPS_BOX
    cands = [s for s in stations() if s["alt"] >= MIN_ALT_M
             and (lat0 <= s["lat"] <= lat1 and lon0 <= s["lon"] <= lon1) != world]
    out = OUT_WORLD if world else OUT
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    rows = []
    for s in cands:
        sid = f"{s['id']}:DE:DWD"
        t = dwd.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS
        # the per-station NWP gate (data/gates/<league>.csv; none for alps yet)
        offset, why = gate.decide("world" if world else "alps", sid, 1) if keep else (None, "")
        keep = keep and offset is not None
        big, bd = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        featured = big is not None and bd <= FEATURED_KM and not world
        print(f"{sid:>12} {s['name']:<32} {s['alt']:6.0f} m days={ok_days} snow_days={snow_days} "
              f"{'featured' if featured else 'network'} {why} -> {'keep' if keep else 'drop'}")
        if keep:
            rows.append({"station_id": sid, "slug": f"dwd-{s['id']}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "Germany" if world else "Bayern", "state": "DE", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "world" if world else "alps",
                         "tier": "featured" if featured else "network", "truth_source": "dwd",
                         "network": "DWD", "external_id": s["id"], "day_offset": offset})
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(cands)} candidates -> {len(rows)} kept "
          f"({sum(r['tier'] == 'featured' for r in rows)} featured) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

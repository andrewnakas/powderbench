"""Generate data/stations/eccc_depth.csv: ECCC climate stations in the
Canadian mountains with a daily snow-on-the-ground reading, for the world
league (truth: the morning-to-morning depth rise, networks/eccc_depth.py).

Candidates: ECCC climate stations with daily data since at least 2022 and a
report since March 2026, that either
  - meet the world league's ski-area rule (>= 400 m or north of 60 N, and
    within SKI_KM of a ski area with SKI_LIFTS+ lifts), or
  - stand at >= MOUNTAIN_M in British Columbia, Yukon or the Northwest
    Territories, or in Alberta west of AB_WEST_LON (Alberta's plains reach
    1,300 m, so the Rocky Mountain front is the line there).
Stations already in the observers league (networks/eccc.py, measured new
snow) are skipped. A candidate is kept when:
  - last winter (Nov-Mar) has >= MIN_DAYS valid days and >= MIN_SNOW_DAYS
    days of 2 cm or more;
  - it has >= MIN_CLIM_WINTERS winters (>= 100 valid Nov-Mar days) since 2010;
  - >= MIN_LIVE_SHARE of the LIVE_DAYS days to two days ago have a value;
  - no better station of the same site (DUP_KM, DUP_ALT_M) is kept, and no
    world-league station is there already;
  - it passes the per-station NWP gate (data/gates/world.csv), which sets its
    day_offset.
None is featured: the Open-Meteo budget does not rise.

    python scripts/networks/eccc_depth.py
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "eccc_depth.csv"
STATIONS_URL = "https://api.weather.gc.ca/collections/climate-stations/items"
PROVINCES = {"BC": "British Columbia", "AB": "Alberta", "YT": "Yukon", "NT": "Northwest Territories",
             "QC": "Quebec", "NB": "New Brunswick", "NS": "Nova Scotia", "NL": "Newfoundland and Labrador",
             "ON": "Ontario"}
MOUNTAIN_PROVINCES = ("BC", "AB", "YT", "NT")
SKI_KM, SKI_LIFTS, SKI_MIN_M, NORTH_LAT = 12.0, 3, 400, 60.0
MOUNTAIN_M = 1200
AB_WEST_LON = -114.3
MIN_DAYS = 100
MIN_SNOW_DAYS = 8
MIN_CLIM_WINTERS = 3
LIVE_DAYS = 14
MIN_LIVE_SHARE = 0.7
DUP_KM = 2.0
DUP_ALT_M = 200
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def pretty(name: str) -> str:
    """ECCC names are upper case: 'BANFF CS' -> 'Banff CS'."""
    keep = {"CS", "RCS", "CDA", "AAFC", "PCC", "MRC", "NCPC", "AUT", "A", "II", "III"}
    return " ".join(w if w.strip("()") in keep else w.capitalize() for w in name.split())


def candidates(today: date) -> list[dict]:
    out = []
    for prov in PROVINCES:
        r = requests.get(STATIONS_URL, params={
            "f": "json", "PROV_STATE_TERR_CODE": prov, "limit": 10000,
            "properties": "CLIMATE_IDENTIFIER,STATION_NAME,ELEVATION,DLY_FIRST_DATE,DLY_LAST_DATE"}, timeout=120)
        r.raise_for_status()
        for f in r.json()["features"]:
            p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
            try:
                alt = float(p.get("ELEVATION") or "nan")
            except ValueError:
                continue
            first, last = (p.get("DLY_FIRST_DATE") or "9")[:10], (p.get("DLY_LAST_DATE") or "")[:10]
            if not alt >= 0 or first > "2022-12-31" or last < f"{today.year if today.month >= 4 else today.year - 1}-03-01":
                continue
            area, d = skiareas.nearest(lat, lon, SKI_LIFTS)
            ski = (alt >= SKI_MIN_M or lat >= NORTH_LAT) and area is not None and d <= SKI_KM
            mountain = alt >= MOUNTAIN_M and prov in MOUNTAIN_PROVINCES and (prov != "AB" or lon <= AB_WEST_LON)
            if ski or mountain:
                out.append({"id": p["CLIMATE_IDENTIFIER"], "name": pretty(p["STATION_NAME"]), "prov": prov,
                            "alt": alt, "lat": lat, "lon": lon})
    return out


def winters_ok(t: pd.DataFrame) -> pd.Series:
    t = t[t["valid"] & pd.to_datetime(t["date"]).dt.month.isin([11, 12, 1, 2, 3])]
    season = [d.year if d.month >= 11 else d.year - 1 for d in t["date"]]
    n = t.assign(season=season).groupby(["station_id", "season"]).size()
    return (n >= 100).groupby(level=0).sum()


def world_sites() -> list[tuple[float, float, float, str]]:
    out = []
    for f in sorted((ROOT / "data" / "stations").glob("*.csv")):
        if f == OUT:
            continue
        for r in csv.DictReader(f.open(encoding="utf-8")):
            if r.get("league") == "world":
                out.append((float(r["latitude"]), float(r["longitude"]), float(r["elevation_ft"]) / 3.28084,
                            r["station_id"]))
    return out


def main() -> int:
    from powderbench.networks import eccc_depth

    today = datetime.now(timezone.utc).date()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    observers = {r["external_id"] for r in csv.DictReader((ROOT / "data" / "stations" / "eccc.csv").open())}
    cands = [c for c in candidates(today) if c["id"] not in observers]
    by_id = {eccc_depth.station_id(c["id"], c["prov"]): c for c in cands}
    ids = list(by_id)
    print(f"{len(ids)} candidates", flush=True)
    last_winter = eccc_depth.daily(ids, b, e)
    ok_days = last_winter.groupby("station_id")["valid"].sum()
    snow_days = (last_winter["snow24"] >= 2 / 2.54).groupby(last_winter["station_id"]).sum()
    winters = pd.concat([winters_ok(eccc_depth.daily(ids, date(y, 11, 1), date(y + 1, 3, 31)))
                         for y in range(2010, last - 1)]).groupby(level=0).sum()
    live_end = today - timedelta(days=2)
    live = eccc_depth.daily(ids, live_end - timedelta(days=LIVE_DAYS - 1), live_end).groupby("station_id")["valid"].mean()
    passed = []
    for sid, c in by_id.items():
        w, d, s, lv = int(winters.get(sid, 0)), int(ok_days.get(sid, 0)), int(snow_days.get(sid, 0)), live.get(sid, 0)
        ok = d >= MIN_DAYS and s >= MIN_SNOW_DAYS and w >= MIN_CLIM_WINTERS and lv >= MIN_LIVE_SHARE
        print(f"{c['name'][:28]:<28} {c['prov']} {c['alt']:5.0f} m days={d} snow_days={s} winters={w} "
              f"live={lv:.2f} -> {'candidate' if ok else 'drop'}", flush=True)
        if ok:
            passed.append((lv, d, sid))
    sites = world_sites()
    unique = []
    for _, _, sid in sorted(passed, reverse=True):  # the most complete record of a site wins
        c = by_id[sid]
        dup = [n[3] for n in sites if abs(n[2] - c["alt"]) <= DUP_ALT_M
               and skiareas.km(c["lat"], c["lon"], n[0], n[1]) <= DUP_KM]
        if dup:
            print(f"{c['name']} -> skip (same site as {dup[0]})", flush=True)
            continue
        unique.append(sid)
        sites.append((c["lat"], c["lon"], c["alt"], sid))
    rows = []
    for sid in unique:
        c = by_id[sid]
        offset, why = gate.decide("world", sid, eccc_depth.DEFAULT_OFFSET)
        print(f"{sid} {c['name']} {why} -> {'keep' if offset is not None else 'drop'}", flush=True)
        if offset is None:
            continue
        rows.append({"station_id": sid, "slug": f"ecccsd-{slugify(c['name'])}", "name": c["name"],
                     "resort": skiareas.label(c["name"], c["lat"], c["lon"], within_km=SKI_KM, min_lifts=SKI_LIFTS),
                     "region": PROVINCES[c["prov"]], "state": c["prov"], "elevation_ft": round(c["alt"] * 3.28084),
                     "latitude": round(c["lat"], 5), "longitude": round(c["lon"], 5), "league": "world",
                     "tier": "network", "truth_source": "eccc_depth", "network": "ECCC",
                     "external_id": c["id"], "day_offset": offset})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(cands)} candidates -> {len(passed)} pass the record tests -> {len(unique)} sites -> "
          f"{len(rows)} kept -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

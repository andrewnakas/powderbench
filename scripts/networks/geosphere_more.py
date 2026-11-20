"""Generate data/stations/geosphere_more.csv: the GeoSphere Austria climate
stations that the alps league's geosphere.csv leaves out.

geosphere.csv takes stations at >= 900 m with a hand-measured new-snow record
and fits their dating to ERA5 (Open-Meteo calls). This list adds, at
>= MIN_ALT_M and with no Open-Meteo calls:
  - hand-measured new snow (`:AT:GEO`, truth_source geosphere) at stations
    not already listed;
  - otherwise the automated snow gauge's 06 UTC depth (`:AT:GEOSH`,
    truth_source geosphere_sh).
A candidate is kept when:
  - last winter (Nov-Mar) has >= MIN_DAYS valid days and >= MIN_SNOW_DAYS days
    of 2 cm or more;
  - it has >= MIN_CLIM_WINTERS winters (>= 100 valid Nov-Mar days) since 2010;
  - it reported on >= MIN_LIVE_SHARE of the LIVE_DAYS days that end LAG_DAYS
    ago;
  - it passes the per-station NWP gate (data/gates/alps.csv), which sets its
    day_offset.
A candidate within DUP_KM and DUP_ALT_M of a station already in the alps
league (any data/stations/*.csv) is skipped. GeoSphere lists some sites twice
(an old and a new station number); of two candidates within DUP_KM and
DUP_ALT_M, the longer record wins, before the gate (so a site that fails it
cannot return under its other number). None is featured: the Open-Meteo budget
does not rise.

    python scripts/networks/geosphere_more.py [other_list.csv ...]

Extra CSVs (e.g. a geosphere.csv from an unmerged branch) are deduped
against as well.
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "geosphere_more.csv"
META = "https://dataset.api.hub.geosphere.at/v1/station/historical/klima-v2-1d/metadata"
MIN_ALT_M = 600
MIN_DAYS = 100
MIN_SNOW_DAYS = 8
MIN_CLIM_WINTERS = 3
LIVE_DAYS = 14
LAG_DAYS = 2
MIN_LIVE_SHARE = 0.7
DUP_KM = 2.0
DUP_ALT_M = 200
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def winters_ok(t: pd.DataFrame) -> int:
    days = [d for d, ok in zip(t["date"], t["valid"]) if ok and d.month in (11, 12, 1, 2, 3)]
    seasons = pd.Series([d.year if d.month >= 11 else d.year - 1 for d in days], dtype="int64")
    return int((seasons.value_counts() >= 100).sum())


def alps_neighbours(extra: list[Path]) -> tuple[list[tuple[float, float, float, str]], set[str]]:
    """(lat, lon, elevation m, id) of every station already in the alps league, and their ids."""
    out, ids = [], set()
    for f in sorted((ROOT / "data" / "stations").glob("*.csv")) + extra:
        if f == OUT:
            continue
        for r in csv.DictReader(f.open(encoding="utf-8")):
            if r.get("league") == "alps":
                out.append((float(r["latitude"]), float(r["longitude"]), float(r["elevation_ft"]) / 3.28084,
                            r["station_id"]))
                ids.add(r["station_id"])
    return out, ids


def main() -> int:
    from powderbench.networks import geosphere, geosphere_sh

    today = datetime.now(timezone.utc).date()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    live_end = today - timedelta(days=LAG_DAYS)
    neighbours, listed = alps_neighbours([Path(a) for a in sys.argv[1:]])
    meta = requests.get(META, headers=geosphere.UA, timeout=120).json()["stations"]
    cands = []
    for s in meta:
        if not s.get("is_active") or (s.get("altitude") or 0) < MIN_ALT_M:
            continue
        if f"{s['id']}:AT:GEO" in listed:
            continue
        dup = [n[3] for n in neighbours if abs(n[2] - s["altitude"]) <= DUP_ALT_M
               and skiareas.km(s["lat"], s["lon"], n[0], n[1]) <= DUP_KM]
        if dup:
            print(f"{s['id']:>6} {s['name'][:26]:<26} -> skip (same site as {dup[0]})", flush=True)
            continue
        cands.append(s)
    # warm both caches in batches (one request per 40 stations per year)
    geo_ids = [f"{s['id']}:AT:GEO" for s in cands]
    sh_ids = [f"{s['id']}:AT:GEOSH" for s in cands]
    geosphere.fetch_snow(geo_ids, date(2010, 1, 1), today)
    geosphere_sh.record(sh_ids, date(2010, 1, 1), today)
    adapters = {"GEO": geosphere, "GEOSH": geosphere_sh}
    rows = []
    for s in cands:
        verdict = None
        for suffix in ("GEO", "GEOSH"):
            sid = f"{s['id']}:AT:{suffix}"
            t = adapters[suffix].daily([sid], b, e)
            ok_days = int(t["valid"].sum())
            snow_days = int((t["snow24"] >= 2 / 2.54).sum())
            if ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS:
                verdict = (sid, suffix, ok_days, snow_days)
                break
        line = f"{s['id']:>6} {s['name'][:26]:<26} {s['altitude']:5.0f} m {s['state'][:12]:<12}"
        if verdict is None:
            print(f"{line} -> drop (last winter)", flush=True)
            continue
        sid, suffix, ok_days, snow_days = verdict
        ad = adapters[suffix]
        winters = winters_ok(ad.daily([sid], date(2010, 11, 1), date(last - 1, 3, 31)))
        live = ad.daily([sid], live_end - timedelta(days=LIVE_DAYS - 1), live_end)["valid"].mean()
        keep = winters >= MIN_CLIM_WINTERS and live >= MIN_LIVE_SHARE
        print(f"{line} {suffix:<5} days={ok_days} snow_days={snow_days} winters={winters} live={live:.2f} "
              f"-> {'candidate' if keep else 'drop'}", flush=True)
        if not keep:
            continue
        name = geosphere_name(s["name"])
        rows.append({"station_id": sid, "slug": f"geo{'sh' if suffix == 'GEOSH' else ''}-{s['id']}", "name": name,
                     "resort": skiareas.label(name, s["lat"], s["lon"], within_km=10.0, min_lifts=3),
                     "region": s["state"], "state": "AT", "elevation_ft": round(s["altitude"] * 3.28084),
                     "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                     "tier": "network", "truth_source": "geosphere" if suffix == "GEO" else "geosphere_sh",
                     "network": "GeoSphere Austria" + (" (snow gauge)" if suffix == "GEOSH" else ""),
                     "external_id": str(s["id"]), "day_offset": None,
                     "_alt": s["altitude"], "_winters": winters, "_days": ok_days})
    rows.sort(key=lambda r: (-r["_winters"], -r["_days"], r["station_id"]))
    unique: list[dict] = []
    for r in rows:
        twin = next((u for u in unique if abs(u["_alt"] - r["_alt"]) <= DUP_ALT_M
                     and skiareas.km(r["latitude"], r["longitude"], u["latitude"], u["longitude"]) <= DUP_KM), None)
        if twin is not None:
            print(f"{r['station_id']} {r['name']} -> skip (same site as {twin['station_id']})", flush=True)
            continue
        unique.append(r)
    # the gate comes after one station per site is chosen, so a site whose
    # station fails cannot come back under its twin number
    gated = []
    for r in unique:
        offset, why = gate.decide("alps", r["station_id"], 1)
        print(f"{r['station_id']} {r['name']} {why} -> {'keep' if offset is not None else 'drop'}", flush=True)
        if offset is not None:
            r["day_offset"] = offset
            gated.append(r)
    unique = gated
    for r in unique:
        for k in ("_alt", "_winters", "_days"):
            r.pop(k)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(unique, key=lambda r: r["name"]))
    n_sh = sum(r["truth_source"] == "geosphere_sh" for r in unique)
    print(f"{len(cands)} candidates -> {len(unique)} kept ({len(unique) - n_sh} hand-measured new snow, "
          f"{n_sh} snow gauge) -> {OUT}")
    return 0


def geosphere_name(name: str) -> str:
    return name.replace("St.", "St. ").replace("  ", " ").strip()


if __name__ == "__main__":
    sys.exit(main())

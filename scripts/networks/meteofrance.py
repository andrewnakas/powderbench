"""Generate data/stations/meteofrance.csv: Météo-France stations with a daily
new-snow board or 06 UTC snow depth in France's mountains outside the Alps
(Pyrenees, Massif Central, Vosges, Jura, Corsica), for the world league.

With --alps: data/stations/meteofrance_alps.csv, the French Alps' climate
stations for the alps league. A candidate within DUP_KM and DUP_ALT_M of a
station already in the alps league (the nivo resort posts) is skipped, and
none is featured (the alps featured set comes from the nivo posts).

In both modes two candidates within DUP_KM and DUP_ALT_M of each other are
one site: the one with the longer record is kept, before the gate.

Candidates: stations in DEPTS at >= MIN_ALT_M. A candidate is kept as a
new-snow station (`:FR:MFHN`) when last winter (Nov-Mar) has >= MIN_DAYS
HNEIGEF readings and >= MIN_SNOW_DAYS of 2 cm or more; otherwise as a depth
station (`:FR:MF`) on the same test applied to its depth-derived new snow.
It also needs to pass the per-station NWP gate (data/gates/world.csv), which
sets its day_offset, and:
  - >= MIN_CLIM_WINTERS winters (>= 100 valid Nov-Mar days) since 2010;
  - to be reporting: a value on >= MIN_LIVE_SHARE of the LIVE_DAYS days that
    end LAG_DAYS ago (the files run two days behind). Seasonal new-snow posts
    (ski-resort observers, December to April) instead need a reading in
    last season's March or later.
Featured: for each ski area with >= FEATURED_LIFTS lifts, its nearest kept
station within FEATURED_KM and no more than BELOW_BASE_M below the lowest
lift; the FEATURED_MAX biggest areas only (the world league's featured set
fell from 5 to 3 with the NWP gate, and the Open-Meteo budget does not rise).

    python scripts/networks/meteofrance.py [--alps]
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "meteofrance.csv"
OUT_ALPS = ROOT / "data" / "stations" / "meteofrance_alps.csv"
ALPS_DEPTS = {
    "74": "Northern French Alps", "73": "Northern French Alps", "38": "Northern French Alps",
    "26": "Northern French Alps",
    "05": "Southern French Alps", "04": "Southern French Alps", "06": "Southern French Alps",
}
DUP_KM = 2.0
DUP_ALT_M = 200
DEPTS = {
    "64": "Pyrenees", "65": "Pyrenees", "31": "Pyrenees", "09": "Pyrenees", "66": "Pyrenees", "11": "Pyrenees",
    "15": "Massif Central", "63": "Massif Central", "43": "Massif Central", "48": "Massif Central",
    "12": "Massif Central", "42": "Massif Central", "07": "Massif Central",
    "88": "Vosges", "68": "Vosges", "67": "Vosges", "90": "Vosges", "70": "Vosges",
    "25": "Jura", "39": "Jura", "01": "Jura",
    "20": "Corsica",
}
MIN_ALT_M = 600
MIN_DAYS = 100
MIN_SNOW_DAYS = 8
MIN_CLIM_WINTERS = 3
LIVE_DAYS = 14
LAG_DAYS = 2
MIN_LIVE_SHARE = 0.7
FEATURED_KM = 6.0
FEATURED_LIFTS = 10
BELOW_BASE_M = 150
FEATURED_MAX = 2
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def metadata(dep: str) -> pd.DataFrame:
    from powderbench.networks import meteofrance as mf

    year = datetime.now(timezone.utc).year
    for pattern in mf._names(dep, "latest", year):
        raw = mf._get(pattern.format("autres-parametres"))
        if raw is not None:
            m = mf.parse(raw, ["NOM_USUEL", "LAT", "LON", "ALTI"])
            return m.drop_duplicates("NUM_POSTE", keep="last")[["NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI"]]
    raise RuntimeError(f"no latest file for {dep}")


def winters_ok(t: pd.DataFrame) -> int:
    days = [d for d, ok in zip(t["date"], t["valid"]) if ok and d.month in (11, 12, 1, 2, 3)]
    seasons = pd.Series([d.year if d.month >= 11 else d.year - 1 for d in days], dtype="int64")
    return int((seasons.value_counts() >= 100).sum())


def alps_neighbours() -> list[tuple[float, float, float, str]]:
    """(lat, lon, elevation m, id) of every station already in the alps league."""
    out = []
    for f in sorted((ROOT / "data" / "stations").glob("*.csv")):
        if f == OUT_ALPS:
            continue
        for r in csv.DictReader(f.open(encoding="utf-8")):
            if r.get("league") == "alps":
                out.append((float(r["latitude"]), float(r["longitude"]), float(r["elevation_ft"]) / 3.28084,
                            r["station_id"]))
    return out


def main() -> int:
    from powderbench.networks import meteofrance as mf

    alps = "--alps" in sys.argv[1:]
    league, depts, out = ("alps", ALPS_DEPTS, OUT_ALPS) if alps else ("world", DEPTS, OUT)
    neighbours = alps_neighbours() if alps else []
    print(f"{league}: {len(neighbours)} stations already in the league to dedupe against", flush=True)
    today = datetime.now(timezone.utc).date()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    live_end = today - timedelta(days=LAG_DAYS)
    rows = []
    for dep, massif in depts.items():
        meta = metadata(dep)
        for _, m in meta[meta["ALTI"] >= MIN_ALT_M].iterrows():
            num = m["NUM_POSTE"]
            dup = [n[3] for n in neighbours if abs(n[2] - float(m["ALTI"])) <= DUP_ALT_M
                   and skiareas.km(float(m["LAT"]), float(m["LON"]), n[0], n[1]) <= DUP_KM]
            if dup:
                print(f"{num} {m['NOM_USUEL'][:26]:<26} -> skip (same site as {dup[0]})", flush=True)
                continue
            verdict = None
            for suffix in ("MFHN", "MF"):
                sid = f"{num}:FR:{suffix}"
                t = mf.daily([sid], b, e)
                ok_days = int(t["valid"].sum())
                snow_days = int((t["snow24"] >= 2 / 2.54).sum())
                if ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS:
                    verdict = (sid, ok_days, snow_days)
                    break
            line = f"{num} {m['NOM_USUEL'][:26]:<26} {m['ALTI']:5.0f} m {massif:<14}"
            if verdict is None:
                print(f"{line} -> drop (last winter)", flush=True)
                continue
            sid, ok_days, snow_days = verdict
            hist = mf.daily([sid], date(2010, 11, 1), date(last - 1, 3, 31))
            winters = winters_ok(hist)
            if sid.endswith("MFHN"):
                rec = mf.record(num)["hn_cm"].dropna()
                live = bool(len(rec)) and max(rec.index) >= date(last, 3, 1)
            else:
                t = mf.daily([sid], live_end - timedelta(days=LIVE_DAYS - 1), live_end)
                live = t["valid"].mean() >= MIN_LIVE_SHARE
            keep = winters >= MIN_CLIM_WINTERS and live
            print(f"{line} {sid.split(':')[-1]:<4} days={ok_days} snow_days={snow_days} winters={winters} "
                  f"live={live} -> {'candidate' if keep else 'drop'}", flush=True)
            if not keep:
                continue
            lat, lon, alt = float(m["LAT"]), float(m["LON"]), float(m["ALTI"])
            name = str(m["NOM_USUEL"]).strip().title()
            rows.append({"station_id": sid, "slug": f"mf-{num}", "name": name,
                         "resort": skiareas.label(name, lat, lon, within_km=10.0, min_lifts=3),
                         "region": massif if alps else "France", "state": "FR", "elevation_ft": round(alt * 3.28084),
                         "latitude": round(lat, 5), "longitude": round(lon, 5), "league": league,
                         "tier": "network", "truth_source": "meteofrance",
                         "network": f"Météo-France ({massif})", "external_id": num,
                         "day_offset": None, "_alt": alt, "_winters": winters, "_days": ok_days})
    # one station per site (Météo-France lists some sites twice, e.g. a
    # climate station and its snow board): the longest record wins
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
    # station fails cannot come back under its twin
    rows = []
    for r in unique:
        offset, why = gate.decide(league, r["station_id"], 0 if r["station_id"].endswith("MFHN") else 1)
        print(f"{r['station_id']} {r['name']} {why} -> {'keep' if offset is not None else 'drop'}", flush=True)
        if offset is not None:
            r["day_offset"] = offset
            rows.append(r)
    nearest: dict[str, tuple[float, str, int, str]] = {}
    for r in rows:
        area, d = skiareas.nearest(r["latitude"], r["longitude"], FEATURED_LIFTS)
        if area is None or d > FEATURED_KM or r["_alt"] < float(area["min_elevation_m"] or 0) - BELOW_BASE_M:
            continue
        if area["id"] not in nearest or d < nearest[area["id"]][0]:
            nearest[area["id"]] = (d, r["station_id"], area["lift_count"], area["name"])
    top = sorted(nearest.values(), key=lambda v: -v[2])[:0 if alps else FEATURED_MAX]
    chosen = {v[1] for v in top}
    for r in rows:
        r["tier"] = "featured" if r["station_id"] in chosen else "network"
        for k in ("_alt", "_winters", "_days"):
            r.pop(k)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(rows)} kept; featured: " + ", ".join(f"{v[3]} ({v[2]} lifts, {v[0]:.1f} km)" for v in top)
          + f" -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate data/stations/ghcnd.csv: US mountain observer stations (NWS COOP +
CoCoRaHS) that measure new snowfall (GHCN-Daily element SNOW), for the
`observers` league.

Selection (all from NCEI's published inventory + station list):
  - reports SNOW, active through the current year, first SNOW <= MAX_FIRST_YEAR
    (enough seasons for a climatology)
  - mountain sites: west of 100W, >= WEST_MIN_M and within NEAR_SNOTEL_KM of a
    SNOTEL site (not the high plains); east, >= EAST_MIN_M and east of 93W
    (Appalachians/Great Lakes snowbelts) — or anywhere within NEAR_SKI_KM of a
    ski area with 2+ lifts (OpenSkiData)
  - region: nearest curated SNOTEL zone within REGION_RADIUS_KM, else state

Featured (ranked set): per curated region, up to FEATURED_PER_REGION of the
highest stations within REGION_RADIUS_KM — plus the eastern ski-country picks.

Second pass: once `powderbench build-climatology --league observers` has run,
re-running this script drops stations whose record is too thin for a winter
climatology (< MIN_WINTER_DOYS Dec-Feb days with an h24 climatology) and only
features stations with a solid one.

    python scripts/networks/ghcnd.py
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "stations" / "ghcnd.csv"
BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
WEST_MIN_M = 1500
EAST_MIN_M = 500
MAX_FIRST_YEAR = 2020
REGION_RADIUS_KM = 60
FEATURED_PER_REGION = 3
CLIMO = ROOT / "data" / "climatology" / "observers.csv.gz"
MIN_WINTER_DOYS = 60  # of the ~90 Dec-Feb days-of-year
NEAR_SKI_KM = 15  # observers this close to a ski area are in at any elevation
NEAR_SNOTEL_KM = 30  # western "mountain" test: near an actual mountain snow site
EAST_MIN_LON = -93.0  # the 500 m eastern rule is for the Appalachians/Great Lakes, not the plains
NEAR_SKI_MIN_LIFTS = 2
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft",
          "latitude", "longitude", "league", "tier", "network", "external_id", "day_offset"]
EAST_REGIONS = {  # state -> ski-country region label for the east
    "VT": "Green Mountains, VT", "NH": "White Mountains, NH", "ME": "Western Maine",
    "NY": "Adirondacks / Catskills, NY", "MA": "Berkshires, MA", "PA": "Poconos / Laurel Highlands, PA",
    "WV": "Allegheny Highlands, WV", "VA": "Blue Ridge, VA", "NC": "Southern Appalachians",
    "TN": "Southern Appalachians", "MI": "Upper Michigan", "WI": "Northwoods, WI", "MN": "Arrowhead, MN",
}


def fetch_text(name: str) -> str:
    for attempt in range(6):
        try:
            r = requests.get(BASE + name, timeout=300)
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            print(f"retry {name}: {exc}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"could not download {name}")


def km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def fit_day_offsets(rows: list[dict]) -> list[dict]:
    """Fit each station's observation-day offset against ERA5 (powderbench.
    networks.attribution); drop stations without a clear fit. Offsets already
    fitted in the current station file are reused — ERA5 calls are costly
    (Open-Meteo bills a winter as ~11 calls per location), so only newly
    admitted stations are fitted, on the last winter."""
    prior = {}
    if OUT.exists():
        with OUT.open() as f:
            prior = {r["station_id"]: int(r["day_offset"]) for r in csv.DictReader(f) if r.get("day_offset")}
    reused = [dict(r, day_offset=prior[r["station_id"]]) for r in rows if r["station_id"] in prior]

    def mountain(r):  # passed the elevation rule -> already judged by an earlier fit
        return r["elevation_ft"] / 3.28084 >= (WEST_MIN_M if r["longitude"] < -100 else EAST_MIN_M)

    rows = [r for r in rows if r["station_id"] not in prior and not mountain(r)]
    print(f"day-offset: reusing {len(reused)} fitted stations, fitting {len(rows)} new")
    if not rows:
        return reused
    sys.path.insert(0, str(ROOT / "src"))
    import pandas as pd

    from powderbench.networks import attribution, ghcnd
    from powderbench.openmeteo import era5_daily_snowfall
    from powderbench.stations import Station

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    sts = [Station(**{k: r[k] for k in ("station_id", "slug", "name", "resort", "region", "state",
                                        "elevation_ft", "latitude", "longitude")}) for r in rows]
    reps, mods = [], []
    for b, e in attribution.winters(last, n=1):
        raw = ghcnd.fetch_snow([s.station_id for s in sts], b, e)
        reps.append(raw[raw["qflag"] == ""])
        mods.append(era5_daily_snowfall(sts, b, e))
    # GHCN dates a reading by when it was taken: same day (evening) or next
    # morning are possible; "before it fell" (-1) means a misdated record
    fits = attribution.fit_offsets(pd.concat(reps), pd.concat(mods), allowed=(0, 1))
    kept = []
    counts = {}
    for r in rows:
        off = fits.get(r["station_id"], {}).get("offset")
        counts[off] = counts.get(off, 0) + 1
        if off is not None:
            r["day_offset"] = off
            kept.append(r)
    print(f"day-offset fit: {counts} (None = no clear fit -> dropped)")
    return reused + kept


def main() -> int:
    this_year = date.today().year
    snow = {}
    for line in fetch_text("ghcnd-inventory.txt").splitlines():
        if line[31:35] == "SNOW":
            first, last = int(line[36:40]), int(line[41:45])
            if line.startswith("US") and last >= this_year and first <= MAX_FIRST_YEAR:
                snow[line[:11]] = first

    solid, climo_ids = None, set()
    if CLIMO.exists():
        import pandas as pd

        c = pd.read_csv(CLIMO, usecols=["station_id", "doy", "h24_mean"]).dropna()
        winter = c[(c["doy"] <= 59) | (c["doy"] >= 335)]
        counts = winter.groupby("station_id").size()
        solid = set(counts[counts >= MIN_WINTER_DOYS].index)
        climo_ids = set(c["station_id"].unique())
        print(f"climatology pass: {len(solid)} stations with a solid winter record")

    with (ROOT / "data" / "stations" / "snotel.csv").open() as f:
        snotel_sites = [(float(r["latitude"]), float(r["longitude"])) for r in csv.DictReader(f)]
    curated = [s for s in yaml.safe_load((ROOT / "data" / "stations.yaml").read_text())["stations"]
               if s.get("league", "stations") == "stations"]
    picked = []
    for line in fetch_text("ghcnd-stations.txt").splitlines():
        gid = line[:11]
        if gid not in snow:
            continue
        lat, lon, elev = float(line[12:20]), float(line[21:30]), float(line[31:37])
        state, name = line[38:40].strip(), line[41:71].strip().title()
        ski, ski_km = skiareas.nearest(lat, lon, NEAR_SKI_MIN_LIFTS)
        near_ski = ski is not None and ski_km <= NEAR_SKI_KM
        if lon < -100:  # west: high AND near a SNOTEL site (excludes the high plains)
            mountain = elev >= WEST_MIN_M and any(
                abs(a - lat) < 0.4 and abs(b - lon) < 0.5 and skiareas.km(lat, lon, a, b) <= NEAR_SNOTEL_KM
                for a, b in snotel_sites)
        else:
            mountain = elev >= EAST_MIN_M and lon >= EAST_MIN_LON
        if not (mountain or near_ski):
            continue
        sid = f"{gid}:{state}:GHCND"
        # climatology pass: stations with a record must be solid; brand-new
        # admissions stay until their climatology exists (then re-run)
        if solid is not None and sid in climo_ids and sid not in solid:
            continue
        near = min(curated, key=lambda c: km(lat, lon, c["latitude"], c["longitude"]))
        dist = km(lat, lon, near["latitude"], near["longitude"])
        if dist <= REGION_RADIUS_KM:
            region = near["region"]
        elif lon >= -100:
            region = EAST_REGIONS.get(state, state)
        else:
            region = state
        picked.append({
            "station_id": f"{gid}:{state}:GHCND", "slug": f"ghcnd-{gid.lower()}", "name": name,
            "resort": skiareas.label(near["resort"] if dist <= REGION_RADIUS_KM else name, lat, lon),
            "region": region,
            "state": state, "elevation_ft": round(elev * 3.28084), "latitude": lat, "longitude": lon,
            "league": "observers", "tier": "network",
            "network": "CoCoRaHS" if gid.startswith("US1") else "COOP", "external_id": gid,
            "_dist": dist,
        })

    picked = fit_day_offsets(picked)

    # featured: the highest few per region (curated zones + eastern ski country)
    by_region: dict[str, list[dict]] = {}
    for p in picked:
        if p["_dist"] <= REGION_RADIUS_KM or p["region"] in EAST_REGIONS.values():
            by_region.setdefault(p["region"], []).append(p)
    for group in by_region.values():
        for p in sorted(group, key=lambda r: -r["elevation_ft"])[:FEATURED_PER_REGION]:
            p["tier"] = "featured"

    picked.sort(key=lambda r: (r["state"], r["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(picked)
    n_feat = sum(p["tier"] == "featured" for p in picked)
    nets = {n: sum(p["network"] == n for p in picked) for n in ("COOP", "CoCoRaHS")}
    print(f"{len(snow)} active US SNOW stations -> {len(picked)} mountain ({nets}), {n_feat} featured -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

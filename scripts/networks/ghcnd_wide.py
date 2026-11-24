"""Generate data/stations/ghcnd_wide.csv: more GHCN-D SNOW observers (NWS COOP
+ CoCoRaHS) for the `observers` league, all network tier, judged by the
hindcast gate (scripts/observers_gate.py) instead of an ERA5 fit.
data/stations/ghcnd.csv (its ids, featured set and day offsets) is not touched.

Two routes, both for active SNOW reporters not in ghcnd.csv:
  wide     admitted only by these looser rules (ghcnd.py's rule does not):
             - first SNOW report <= WIDE_MAX_FIRST_YEAR (ghcnd.py: 2020);
             - snow country: west of 100W within ghcnd.NEAR_SNOTEL_KM of a
               SNOTEL site, east of it east of 93W (ghcnd.py's guards against
               the plains);
             - and any of: west >= WIDE_WEST_MIN_M; Alaska at any elevation;
               east >= ghcnd.EAST_MIN_M; within WIDE_NEAR_SKI_KM of a ski
               area with 2+ lifts.
  rejudge  admitted by ghcnd.py's rule at mountain elevation but left out of
           ghcnd.csv: the 2-winter ERA5 day-offset fit (2026-10-20) found no
           clear day, and ghcnd.py never fits them again. ERA5's ~31 km cells
           are coarse for mountain points, so the 9 km gate judges them again.
A candidate is kept when it passes the gate (data/gates/observers.csv, which
also sets its day_offset) and, once its climatology is built, ghcnd.py's
climatology pass (a solid Dec-Feb record) — and, on the wide route only,
a mean of >= MIN_SEASON_IN of new snow per Oct-May season there (snow
country, not low-snow climates). A candidate without a gate row is held back.
The climatology pass records each gate pass's numbers in RECORD and removes
the climatology rows of the stations it drops (no other row changes), so a
later re-run drops them again without their rows.

    python scripts/networks/ghcnd_wide.py --candidates /tmp/candidates.csv   # the gate's input
    python scripts/observers_gate.py /tmp/candidates.csv --season 2024 --season 2025 --season 2026 \\
        --out data/gates/observers.csv
    python scripts/networks/ghcnd_wide.py      # gate passes -> ghcnd_wide.csv
    powderbench build-climatology --league observers --only-missing
    python scripts/networks/ghcnd_wide.py      # + the climatology pass
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ghcnd  # noqa: E402
import skiareas  # noqa: E402

ROOT = ghcnd.ROOT
OUT = ROOT / "data" / "stations" / "ghcnd_wide.csv"
GATE = ROOT / "data" / "gates" / "observers.csv"
RECORD = ROOT / "data" / "gates" / "observers_climatology.csv"  # the climatology pass, per gate pass
WIDE_MAX_FIRST_YEAR = 2023  # newer CoCoRaHS observers; the climatology pass still drops thin records
WIDE_WEST_MIN_M = 1200
WIDE_NEAR_SKI_KM = 25
MIN_SEASON_IN = 40.0  # wide route: mean Oct-May new snow in the station's own climatology


def route(lat: float, lon: float, elev: float, state: str, first: int, ski_km: float, near_snotel: bool) -> str | None:
    """'base' when ghcnd.py's rule admits the station, 'wide' when only the
    looser rules here do, else None. `near_snotel`: ghcnd.py's test (a SNOTEL
    site within NEAR_SNOTEL_KM)."""
    west = lon < -100
    mountain = (elev >= ghcnd.WEST_MIN_M and near_snotel) if west else (elev >= ghcnd.EAST_MIN_M and lon >= ghcnd.EAST_MIN_LON)
    if first <= ghcnd.MAX_FIRST_YEAR and (mountain or ski_km <= ghcnd.NEAR_SKI_KM):
        return "base"
    if first > WIDE_MAX_FIRST_YEAR or not (near_snotel if west else lon >= ghcnd.EAST_MIN_LON):
        return None
    high = (elev >= WIDE_WEST_MIN_M or state == "AK") if west else elev >= ghcnd.EAST_MIN_M
    return "wide" if high or ski_km <= WIDE_NEAR_SKI_KM else None


def mountain_elevation(elev: float, lon: float) -> bool:
    """ghcnd.fit_day_offsets' test for "already judged by an earlier fit"."""
    return elev >= (ghcnd.WEST_MIN_M if lon < -100 else ghcnd.EAST_MIN_M)


def climatology_stats() -> dict[str, tuple[int, float]] | None:
    """Per station with climatology rows: (Dec-Feb days-of-year with an h24
    mean, as in ghcnd.py's climatology pass; mean Oct-May season snowfall in
    inches). Rows without any h24 mean count as (0, 0.0). None without a
    climatology file."""
    if not ghcnd.CLIMO.exists():
        return None
    import pandas as pd

    c = pd.read_csv(ghcnd.CLIMO, usecols=["station_id", "doy", "h24_mean"])
    ids = c["station_id"].unique()
    c = c.dropna()
    winter = c[(c["doy"] <= 59) | (c["doy"] >= 335)].groupby("station_id").size()
    season = c[(c["doy"] >= 274) | (c["doy"] <= 151)].groupby("station_id")["h24_mean"].sum()
    return {sid: (int(winter.get(sid, 0)), round(float(season.get(sid, 0.0)), 2)) for sid in ids}


def climatology_verdict(route: str, stats: tuple[int, float] | None) -> str | None:
    """Why the climatology pass drops a gate pass, or None to keep it (also
    while its climatology is not built yet)."""
    if stats is None:
        return None
    winter_doys, season_in = stats
    if winter_doys < ghcnd.MIN_WINTER_DOYS:
        return "thin climatology"
    if route == "wide" and season_in < MIN_SEASON_IN:
        return f"< {MIN_SEASON_IN:.0f} in a season"
    return None


def read_record() -> dict[str, tuple[int, float]]:
    if not RECORD.exists():
        return {}
    with RECORD.open(encoding="utf-8") as f:
        return {r["station_id"]: (int(r["winter_doys"]), float(r["season_in"])) for r in csv.DictReader(f)}


def prune_climatology(drop: set[str]) -> int:
    """Remove the climatology rows of `drop`; every other row stays as written."""
    import pandas as pd

    c = pd.read_csv(ghcnd.CLIMO, dtype=str, keep_default_na=False)
    gone = c["station_id"].isin(drop)
    if gone.any():
        c[~gone].to_csv(ghcnd.CLIMO, index=False)
    return int(gone.sum())


def read_gate() -> dict[str, dict]:
    if not GATE.exists():
        return {}
    with GATE.open(encoding="utf-8") as f:
        return {r["station_id"]: r for r in csv.DictReader(f)}


def candidates() -> list[dict]:
    """Every wide and rejudge candidate, with its station-file fields."""
    this_year = date.today().year
    first = {}
    for line in ghcnd.fetch_text("ghcnd-inventory.txt").splitlines():
        if line[31:35] == "SNOW" and line.startswith("US"):
            f, last = int(line[36:40]), int(line[41:45])
            if last >= this_year and f <= max(WIDE_MAX_FIRST_YEAR, ghcnd.MAX_FIRST_YEAR):
                first[line[:11]] = f
    with (ROOT / "data" / "stations" / "snotel.csv").open() as f:
        snotel_sites = [(float(r["latitude"]), float(r["longitude"])) for r in csv.DictReader(f)]
    with ghcnd.OUT.open() as f:
        in_base_file = {r["station_id"] for r in csv.DictReader(f)}
    import yaml

    curated = [s for s in yaml.safe_load((ROOT / "data" / "stations.yaml").read_text())["stations"]
               if s.get("league", "stations") == "stations"]
    out = []
    for line in ghcnd.fetch_text("ghcnd-stations.txt").splitlines():
        gid = line[:11]
        if gid not in first:
            continue
        lat, lon, elev = float(line[12:20]), float(line[21:30]), float(line[31:37])
        state, name = line[38:40].strip(), line[41:71].strip().title()
        sid = f"{gid}:{state}:GHCND"
        if sid in in_base_file:
            continue
        ski, ski_km = skiareas.nearest(lat, lon, ghcnd.NEAR_SKI_MIN_LIFTS)
        near_snotel = any(abs(p - lat) < 0.4 and abs(q - lon) < 0.5 and skiareas.km(lat, lon, p, q) <= ghcnd.NEAR_SNOTEL_KM
                          for p, q in snotel_sites)
        r = route(lat, lon, elev, state, first[gid], ski_km if ski else float("inf"), near_snotel)
        if r == "base":
            if not mountain_elevation(elev, lon):
                continue  # ghcnd.py itself fits these again on every run
            r = "rejudge"
        elif r is None:
            continue
        near = min(curated, key=lambda c: ghcnd.km(lat, lon, c["latitude"], c["longitude"]))
        dist = ghcnd.km(lat, lon, near["latitude"], near["longitude"])
        region = (near["region"] if dist <= ghcnd.REGION_RADIUS_KM
                  else ghcnd.EAST_REGIONS.get(state, state) if lon >= -100 else state)
        out.append({
            "station_id": sid, "slug": f"ghcnd-{gid.lower()}", "name": name,
            "resort": skiareas.label(near["resort"] if dist <= ghcnd.REGION_RADIUS_KM else name, lat, lon),
            "region": region, "state": state, "elevation_ft": round(elev * 3.28084), "latitude": lat,
            "longitude": lon, "league": "observers", "tier": "network",
            "network": "CoCoRaHS" if gid.startswith("US1") else "COOP", "external_id": gid, "route": r,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidates", help="write every candidate to this CSV (the gate's input) and stop")
    a = ap.parse_args()

    cands = candidates()
    if a.candidates:
        with open(a.candidates, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cands[0]))
            w.writeheader()
            w.writerows(cands)
        n = {r: sum(c["route"] == r for c in cands) for r in ("wide", "rejudge")}
        print(f"{len(cands)} candidates {n} -> {a.candidates}")
        return 0

    gate, live, recorded = read_gate(), climatology_stats(), read_record()
    kept, counts, record, drop = [], {}, {}, set()

    def count(key):
        counts[key] = counts.get(key, 0) + 1

    for c in cands:
        g = gate.get(c["station_id"])
        if g is None:
            count(f"{c['route']}: not gated yet -> hold")
            continue
        if g["verdict"] != "pass":
            count(f"{c['route']}: gate {g['verdict']}")
            continue
        sid = c["station_id"]
        stats = live[sid] if live is not None and sid in live else recorded.get(sid)
        why = climatology_verdict(c["route"], stats)
        if stats is not None:
            record[sid] = (c["route"], *stats, why or "kept")
        if why:
            count(f"{c['route']}: {why}")
            drop.add(sid)
            continue
        count(f"{c['route']}: kept")
        kept.append(dict(c, day_offset=int(float(g["day_offset"]))))

    if record:
        with RECORD.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["station_id", "route", "winter_doys", "season_in", "verdict"])
            w.writerows([sid, *record[sid]] for sid in sorted(record))
    if live is not None and drop & set(live):
        print(f"climatology: removed {prune_climatology(drop)} rows of {len(drop & set(live))} dropped stations")

    kept.sort(key=lambda r: (r["state"], r["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ghcnd.FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
    print(f"{dict(sorted(counts.items()))} -> {len(kept)} stations -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

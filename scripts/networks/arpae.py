"""Generate data/stations/arpae.csv: Arpae Emilia-Romagna's automatic snow
stations in the northern Apennines (snow depth), for the world league.

Candidates: the stations with snow depth last winter at >= MIN_ALT_M. A
candidate is kept when:
  - last winter (Nov-Mar) has >= MIN_DAYS valid days and >= MIN_SNOW_DAYS days
    of 2 cm or more (depth-derived new snow);
  - it has >= MIN_CLIM_WINTERS winters (>= 100 valid Nov-Mar days) since 2010;
  - >= MIN_LIVE_SHARE of the LIVE_DAYS days to two days ago have a value;
  - it passes the per-station NWP gate (data/gates/world.csv), which sets its
    day_offset.
A candidate within DUP_KM and DUP_ALT_M of a station already in the world
league is skipped. None is featured: the Open-Meteo budget does not rise.

    python scripts/networks/arpae.py
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "arpae.csv"
MIN_ALT_M = 600
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


def winters_ok(t: pd.DataFrame) -> pd.Series:
    t = t[t["valid"] & pd.to_datetime(t["date"]).dt.month.isin([11, 12, 1, 2, 3])]
    season = [d.year if d.month >= 11 else d.year - 1 for d in t["date"]]
    n = t.assign(season=season).groupby(["station_id", "season"]).size()
    return (n >= 100).groupby(level=0).sum()


def neighbours() -> list[tuple[float, float, float, str]]:
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
    from powderbench.networks import arpae

    today = datetime.now(timezone.utc).date()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    meta = arpae.readings(b, e).drop_duplicates("sid", keep="last").set_index("sid")
    meta = meta[meta["height_m"] >= MIN_ALT_M]
    near = neighbours()
    cands = []
    for sid, m in meta.sort_values("height_m", ascending=False).iterrows():
        line = f"{str(m['name'])[:28]:<28} {m['height_m']:5.0f} m"
        dup = [n[3] for n in near if abs(n[2] - m["height_m"]) <= DUP_ALT_M
               and skiareas.km(m["lat"], m["lon"], n[0], n[1]) <= DUP_KM]
        if dup:
            print(f"{line} -> skip (same site as {dup[0]})", flush=True)
            continue
        cands.append(sid)
    ids = list(cands)
    last_winter = arpae.daily(ids, b, e)
    ok_days = last_winter.groupby("station_id")["valid"].sum()
    snow_days = (last_winter["snow24"] >= 2 / 2.54).groupby(last_winter["station_id"]).sum()
    winters = pd.concat([winters_ok(arpae.daily(ids, date(y, 11, 1), date(y + 1, 3, 31)))
                         for y in range(2010, last - 1)]).groupby(level=0).sum()
    live_end = today - timedelta(days=2)
    live = arpae.daily(ids, live_end - timedelta(days=LIVE_DAYS - 1), live_end).groupby("station_id")["valid"].mean()
    rows = []
    for sid in cands:
        m = meta.loc[sid]
        name = str(m["name"]).strip()
        w, d, s, lv = int(winters.get(sid, 0)), int(ok_days.get(sid, 0)), int(snow_days.get(sid, 0)), live.get(sid, 0)
        keep = d >= MIN_DAYS and s >= MIN_SNOW_DAYS and w >= MIN_CLIM_WINTERS and lv >= MIN_LIVE_SHARE
        offset, why = gate.decide("world", sid, 1) if keep else (None, "")
        keep = keep and offset is not None
        print(f"{name[:28]:<28} {m['height_m']:5.0f} m days={d} snow_days={s} winters={w} live={lv:.2f} {why} "
              f"-> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            network, lon, lat = sid.removesuffix(arpae.SUFFIX).rsplit("-", 2)
            rows.append({"station_id": sid, "slug": f"arpae-{slugify(name)}", "name": name,
                         "resort": skiareas.label(name, m["lat"], m["lon"], within_km=10.0, min_lifts=3),
                         "region": "Northern Apennines", "state": "IT",
                         "elevation_ft": round(m["height_m"] * 3.28084),
                         "latitude": round(m["lat"], 5), "longitude": round(m["lon"], 5), "league": "world",
                         "tier": "network", "truth_source": "arpae", "network": "Arpae Emilia-Romagna",
                         "external_id": f"{network}/{lon}/{lat}", "day_offset": offset})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(cands)} candidates -> {len(rows)} kept -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

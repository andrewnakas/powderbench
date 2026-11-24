"""Generate data/stations/arpapie.csv: ARPA Piemonte's automatic snow
stations (validated 08:00 snow depth), for the alps league.

Candidates: the stations in the live feed (api_realtime/pie_neve) at
>= MIN_ALT_M. A candidate is kept when:
  - last winter (Nov-Mar) has >= MIN_DAYS valid days and >= MIN_SNOW_DAYS days
    of 2 cm or more (depth-derived new snow);
  - it has >= MIN_CLIM_WINTERS winters (>= 100 valid Nov-Mar days) since 2010;
  - the live feed has a reading on >= MIN_LIVE_SHARE of its 7 days;
  - it passes the per-station NWP gate (data/gates/alps.csv), which sets its
    day_offset.
A candidate within DUP_KM and DUP_ALT_M of a station already in the alps
league is skipped. None is featured: the Open-Meteo budget does not rise.

    python scripts/networks/arpapie.py [other_list.csv ...]
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "arpapie.csv"
MIN_ALT_M = 600
MIN_DAYS = 100
MIN_SNOW_DAYS = 8
MIN_CLIM_WINTERS = 3
MIN_LIVE_SHARE = 0.7
DUP_KM = 2.0
DUP_ALT_M = 200
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def winters_ok(t: pd.DataFrame) -> int:
    days = [d for d, ok in zip(t["date"], t["valid"]) if ok and d.month in (11, 12, 1, 2, 3)]
    seasons = pd.Series([d.year if d.month >= 11 else d.year - 1 for d in days], dtype="int64")
    return int((seasons.value_counts() >= 100).sum())


def alps_neighbours(extra: list[Path]) -> list[tuple[float, float, float, str]]:
    out = []
    for f in sorted((ROOT / "data" / "stations").glob("*.csv")) + extra:
        if f == OUT:
            continue
        for r in csv.DictReader(f.open(encoding="utf-8")):
            if r.get("league") == "alps":
                out.append((float(r["latitude"]), float(r["longitude"]), float(r["elevation_ft"]) / 3.28084,
                            r["station_id"]))
    return out


def main() -> int:
    from powderbench.networks import arpapie

    today = datetime.now(timezone.utc).date()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    neighbours = alps_neighbours([Path(a) for a in sys.argv[1:]])
    lv = arpapie.live()
    window = sorted(lv["date"].unique())[-7:]
    in_live = set(lv["station_code"])
    rows = []
    for st in arpapie.registry():
        c = str(st["station_code"])
        if st.get("id_network") != "PIE" or c not in in_live or (st.get("quote") or 0) < MIN_ALT_M:
            continue
        lat, lon, alt = float(st["lat"]), float(st["lng"]), float(st["quote"])
        name = str(st["name"]).strip().title()
        line = f"{c:>4} {name[:28]:<28} {alt:5.0f} m"
        dup = [n[3] for n in neighbours if abs(n[2] - alt) <= DUP_ALT_M and skiareas.km(lat, lon, n[0], n[1]) <= DUP_KM]
        if dup:
            print(f"{line} -> skip (same site as {dup[0]})", flush=True)
            continue
        sid = f"{c}:IT:PIE"
        t = arpapie.daily([sid], b, e)
        ok_days, snow_days = int(t["valid"].sum()), int((t["snow24"] >= 2 / 2.54).sum())
        winters = winters_ok(arpapie.daily([sid], date(2010, 11, 1), date(last - 1, 3, 31)))
        mine = lv[(lv["station_code"] == c) & lv["date"].isin(window)]["hs_cm"]
        live = mine.notna().sum() / max(len(window), 1)
        keep = (ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and winters >= MIN_CLIM_WINTERS
                and live >= MIN_LIVE_SHARE)
        offset, why = gate.decide("alps", sid, 1) if keep else (None, "")
        keep = keep and offset is not None
        print(f"{line} days={ok_days} snow_days={snow_days} winters={winters} live={live:.2f} {why} "
              f"-> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            rows.append({"station_id": sid, "slug": f"pie-{c}", "name": name,
                         "resort": skiareas.label(name, lat, lon, within_km=10.0, min_lifts=3),
                         "region": "Piedmont", "state": "IT", "elevation_ft": round(alt * 3.28084),
                         "latitude": round(lat, 5), "longitude": round(lon, 5), "league": "alps",
                         "tier": "network", "truth_source": "arpapie", "network": "ARPA Piemonte",
                         "external_id": arpapie.history_key(st), "day_offset": offset})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(rows)} kept -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

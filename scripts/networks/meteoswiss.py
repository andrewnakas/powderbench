"""Generate data/stations/meteoswiss.csv: MeteoSwiss manual stations with a
daily new-snow reading (hns000d0) near Swiss ski areas, for the alps league.

Candidates: active ogd-nime stations carrying hns000d0, at >= MIN_ALT_M and
within NEAR_KM of a ski area with >= NEAR_LIFTS lifts (OpenSkiData).
Dropped when an alps station already sits within DUP_KM (most often the
SLF study plot at the same village: one site, one truth).
Kept when last winter (Nov-Mar) has >= MIN_DAYS readings and >= MIN_SNOW_DAYS
days of >= 2 cm. All network tier: featured stations draw on Open-Meteo's
free quota and SLF already covers the big Swiss areas.

Day attribution: MeteoSwiss dates the 06-06 UTC reading by its first day
(offset 0). Checked per station: the new snow dated D has to track the
station's own depth rise from D to D+1 better than the rise from D-1 to D.

    python scripts/networks/meteoswiss.py
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "meteoswiss.csv"
META = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-nime/ogd-nime_meta_stations.csv"
INVENTORY = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-nime/ogd-nime_meta_datainventory.csv"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
MIN_ALT_M = 800
NEAR_KM = 10.0
NEAR_LIFTS = 5
DUP_KM = 1.0
MIN_DAYS = 120  # of 151
MIN_SNOW_DAYS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def _csv(url: str) -> pd.DataFrame:
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content), sep=";", encoding="latin1", dtype=str)


def candidates() -> list[dict]:
    inv = _csv(INVENTORY)
    live = inv[(inv["parameter_shortname"] == "hns000d0") & inv["data_till"].isna()]["station_abbr"]
    meta = _csv(META)
    meta = meta[meta["station_abbr"].isin(set(live))]
    out = []
    for _, m in meta.iterrows():
        out.append({"id": m["station_abbr"], "name": m["station_name"], "canton": m["station_canton"],
                    "alt": float(m["station_height_masl"]), "lat": float(m["station_coordinates_wgs84_lat"]),
                    "lon": float(m["station_coordinates_wgs84_lon"])})
    return out


def dating_check(rec: pd.DataFrame, b: date, e: date) -> tuple[float, float]:
    """corr(new snow dated D, depth rise D->D+1) and the same against D-1->D."""
    r = rec[(rec.index >= b) & (rec.index <= e)].copy()
    r.index = pd.to_datetime(r.index)
    r = r.asfreq("D")
    fwd = r["hs_cm"].shift(-1) - r["hs_cm"]
    back = r["hs_cm"] - r["hs_cm"].shift(1)
    return (round(float(r["hn_cm"].corr(fwd.clip(lower=0))), 3), round(float(r["hn_cm"].corr(back.clip(lower=0))), 3))


def main() -> int:
    from powderbench.networks import meteoswiss
    from powderbench.stations import load_stations

    have = [(s.station_id, s.latitude, s.longitude) for s in load_stations("alps")
            if not s.station_id.endswith(":CH:MCH")]
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    rows = []
    for s in candidates():
        if s["alt"] < MIN_ALT_M:
            continue
        area, ad = skiareas.nearest(s["lat"], s["lon"], NEAR_LIFTS)
        if area is None or ad > NEAR_KM:
            continue
        dup = [sid for sid, la, lo in have if skiareas.km(s["lat"], s["lon"], la, lo) < DUP_KM]
        sid = f"{s['id']}:CH:MCH"
        if dup:
            print(f"{sid:>10} {s['name']:<28} duplicate of {dup[0]} -> drop")
            continue
        t = meteoswiss.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        fwd, back = dating_check(meteoswiss.record(s["id"]), b, e)
        dated_ok = fwd > back
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and dated_ok
        print(f"{sid:>10} {s['name']:<28} {s['alt']:6.0f} m days={ok_days} snow_days={snow_days} "
              f"rise corr D->D+1 {fwd:.2f} vs D-1->D {back:.2f} near={area['name']} ({ad:.1f} km) "
              f"-> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            rows.append({"station_id": sid, "slug": f"mch-{s['id'].lower()}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=NEAR_KM,
                                                  min_lifts=NEAR_LIFTS),
                         "region": s["canton"], "state": "CH", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "alps",
                         "tier": "network", "truth_source": "meteoswiss", "network": "MeteoSwiss",
                         "external_id": s["id"], "day_offset": 0})
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"-> {len(rows)} kept -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

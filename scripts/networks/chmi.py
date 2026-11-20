"""Generate data/stations/chmi.csv: Czech climate stations with an observer's
new-snow reading (SNO) near ski areas, for the world league.

Candidates: active ČHMÚ stations (metadata meta1.csv) with a historical SNO
file, at >= MIN_ALT_M and within NEAR_KM of a ski area with >= NEAR_LIFTS
lifts (OpenSkiData).
Kept when last winter (Nov-Mar) has >= MIN_DAYS readings and >= MIN_SNOW_DAYS
days of >= 2 cm, and the dating is clear. Stations differ: most file the
07:00 reading under the day the snow fell (the value dated D tracks the
station's own depth rise from D to D+1: offset 0), some under the day it is
read (it tracks D-1 -> D: offset 1). The offset is the one whose
correlation wins by >= DATING_MARGIN; otherwise the station is left out.
Live: the station's latest reading is at most LIVE_DAYS old (the volunteer
observers' monthly reports arrive weeks late; run this in season).
A kept station also needs >= MIN_CLIM_WINTERS winters (>= 100 Nov-Mar readings)
inside the climatology window, or the league's climatology has no forecast for
it (Praděd's record stops in 1998 and resumes in 2025).
Featured: for each ski area with >= FEATURED_LIFTS lifts, its nearest kept
station within FEATURED_KM.

    python scripts/networks/chmi.py
"""

from __future__ import annotations

import csv
import io
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "chmi.csv"
META = "https://opendata.chmi.cz/meteorology/climate/historical_csv/metadata/meta1.csv"
LISTING = "https://opendata.chmi.cz/meteorology/climate/historical_csv/data/daily/snow/"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
MIN_ALT_M = 600
NEAR_KM = 8.0
NEAR_LIFTS = 5
MIN_DAYS = 120  # of 151
MIN_SNOW_DAYS = 10
DUP_KM = 0.5
DATING_MARGIN = 0.1
MIN_CLIM_WINTERS = 3
LIVE_DAYS = 7
FEATURED_KM = 5.0
FEATURED_LIFTS = 10
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def candidates() -> list[dict]:
    html = requests.get(LISTING, headers=UA, timeout=120).text
    with_sno = set(re.findall(r"dly-(0-[0-9-]+)-SNO\.csv", html))
    meta = pd.read_csv(io.StringIO(requests.get(META, headers=UA, timeout=120).text), dtype=str)
    meta = meta[meta["END_DATE"].str.startswith("3999") & meta["WSI"].isin(with_sno)]
    out = []
    for _, m in meta.drop_duplicates("WSI", keep="last").iterrows():
        lat, lon, alt = float(m["GEOGR2"]), float(m["GEOGR1"]), float(m["ELEVATION"])
        if alt < MIN_ALT_M:
            continue
        area, d = skiareas.nearest(lat, lon, NEAR_LIFTS)
        if area is None or d > NEAR_KM:
            continue
        out.append({"wsi": m["WSI"], "name": re.sub(r"\s{2,}", " – ", m["FULL_NAME"].strip()), "lat": lat, "lon": lon, "alt": alt,
                    "area": area["name"], "km": d})
    return out


def dating(rec: pd.DataFrame, b: date, e: date) -> tuple[float, float]:
    """corr(new snow dated D, depth rise D-1->D) and against D->D+1."""
    r = rec[(rec.index >= b) & (rec.index <= e)].copy()
    r.index = pd.to_datetime(r.index)
    r = r.asfreq("D")
    back = (r["SCE"] - r["SCE"].shift(1)).clip(lower=0)
    fwd = (r["SCE"].shift(-1) - r["SCE"]).clip(lower=0)
    return round(float(r["SNO"].corr(back)), 3), round(float(r["SNO"].corr(fwd)), 3)


def clim_winters(sno: pd.Series) -> int:
    """Winters (Nov-Mar) with >= 100 readings inside the climatology window."""
    from powderbench.truth_sources import _CLIM_END

    s = sno.dropna()
    s = s[(s.index >= date(2000, 10, 1)) & (s.index <= _CLIM_END)]
    d = pd.to_datetime(pd.Series(s.index))
    d = d[d.dt.month.isin([11, 12, 1, 2, 3])]
    return int(((d.dt.year - (d.dt.month < 11)).value_counts() >= 100).sum())


def main() -> int:
    from powderbench.networks import chmi

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    rows = []
    for s in candidates():
        sid = f"{s['wsi']}:CZ:CHMI"
        t = chmi.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        back, fwd = dating(chmi.record(s["wsi"]), b, e)
        offset = 1 if back - fwd >= DATING_MARGIN else 0 if fwd - back >= DATING_MARGIN else None
        if offset is not None and offset != chmi.DEFAULT_OFFSET:
            t = chmi.daily([sid], b, e, {sid: offset})
            ok_days = int(t["valid"].sum())
            snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        sno = chmi.record(s["wsi"])["SNO"].dropna()
        winters = clim_winters(sno)
        lag = (today - max(sno.index)).days if len(sno) else None
        keep = (ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and offset is not None
                and winters >= MIN_CLIM_WINTERS and lag is not None and lag <= LIVE_DAYS)
        print(f"{sid:>26} {s['name'][:26]:<26} {s['alt']:5.0f} m days={ok_days} snow_days={snow_days} "
              f"rise corr D-1->D {back:.2f} vs D->D+1 {fwd:.2f} near={s['area'][:26]} ({s['km']:.1f} km) "
              f"offset={offset} winters={winters} lag={lag} -> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            rows.append({"station_id": sid, "slug": f"chmi-{s['wsi'].rsplit('-', 1)[-1].lower()}",
                         "name": s["name"], "resort": skiareas.label(s["name"], s["lat"], s["lon"],
                                                                      within_km=NEAR_KM, min_lifts=NEAR_LIFTS),
                         "region": "Czechia", "state": "CZ", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "world",
                         "tier": "network", "truth_source": "chmi", "network": "ČHMÚ", "external_id": s["wsi"],
                         "day_offset": offset, "_days": ok_days})
    rows.sort(key=lambda r: -r["_days"])
    kept: list[dict] = []
    for r in rows:
        if all(skiareas.km(r["latitude"], r["longitude"], k["latitude"], k["longitude"]) >= DUP_KM for k in kept):
            kept.append(r)
    nearest: dict[str, tuple[float, str, str]] = {}
    for r in kept:
        area, d = skiareas.nearest(r["latitude"], r["longitude"], FEATURED_LIFTS)
        if area is not None and d <= FEATURED_KM and (area["id"] not in nearest or d < nearest[area["id"]][0]):
            nearest[area["id"]] = (d, r["station_id"], area["name"])
    chosen = {v[1]: v[2] for v in nearest.values()}
    for r in kept:
        if r["station_id"] in chosen:
            r["tier"] = "featured"
            r["resort"] = chosen[r["station_id"]]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: r["name"]))
    print(f"-> {len(kept)} kept ({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

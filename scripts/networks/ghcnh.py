"""Generate data/stations/ghcnh.csv: WMO weather stations (NOAA GHCNh) with a
morning snow-depth report near ski areas outside the regions PowderBench
already covers with national networks, for the world league.

Candidates: GHCNh stations outside COVERED, at >= MIN_ALT_M (or north of
60 N, where the snow lies at sea level), within NEAR_KM of a ski area with
>= NEAR_LIFTS lifts (OpenSkiData).
Kept when last winter (Nov-Mar) has >= MIN_DAYS confirmed morning-to-
morning depth rises and >= MIN_SNOW_DAYS days of >= 2 cm, and the pack is
plausible: at >= HIGH_M the Dec-Feb morning depth is above 0 on at least
MIN_COVER of the days with a report, and anywhere the Dec-Feb record has at
most MAX_DROPOUTS zero mornings inside a pack (networks/ghcnh.dropouts:
Kasprowy Wierch and Kredarica filed 0 under a metre of snow on many days of
January 2026, and the climb back reads as a storm).
Live: GHCNh's current-year files carry far fewer reports for some countries
than the same months do once NOAA backfills them (Norway's MET feed, Spain):
a kept station needs a morning report on >= MIN_LIVE_SHARE of the LIVE_DAYS
days ending LAG_DAYS ago, as read today (run this in season; a feed outage
on every station, as on 2026-11-10..12, counts against all alike).
A kept station also needs >= MIN_CLIM_WINTERS winters (>= 100 valid Nov-Mar
days) inside the climatology window, or the league's climatology has no
forecast for it (Lesko's depth reports before 2023 come only from the unusable
IMGW feed).
NWP_GATE_FAILED lists stations whose truth failed the per-station NWP gate
(scripts/nwp_gate.py on the direct-NWP branch: daily truth vs ECMWF IFS over
winter 2025-26, best day shift below r 0.4). Summit stations fail it: wind,
not snowfall, moves their depth.
Featured: for each ski area with >= FEATURED_LIFTS lifts, its nearest kept
station within FEATURED_KM and no more than BELOW_BASE_M below the area's
lowest lift (a valley-floor station says little about the slopes).

    python scripts/networks/ghcnh.py --list <ghcnh-station-list.csv>
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "ghcnh.csv"
LIST_URL = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/ghcnh-station-list.csv"
# national networks already in PowderBench (US/CA observers and telemetry,
# JMA, SLF/MeteoSwiss, GeoSphere, DWD, Météo-France, Italian regions, SMHI, FMI,
# and ČHMÚ in this league: its observers' new snow beats the same sites' SYNOP depth)
COVERED = {"US", "CA", "JP", "CH", "AT", "DE", "FR", "IT", "LI", "SE", "FI", "MC", "CZ"}
MIN_ALT_M = 400
NEAR_KM = 12.0
NEAR_LIFTS = 3
MIN_DAYS = 100  # of 151
MIN_SNOW_DAYS = 8
HIGH_M = 1200
MIN_COVER = 0.5
MAX_DROPOUTS = 3
MIN_CLIM_WINTERS = 3
LIVE_DAYS = 14
LAG_DAYS = 3
MIN_LIVE_SHARE = 0.7
FEATURED_KM = 6.0
# r vs ECMWF IFS, 2025-12-01..2026-03-15, shifts -1/0/+1 (n ~90-105 days)
NWP_GATE_FAILED = {
    "LOM00011916": "Chopok summit: r 0.28/0.27/0.07, truth 25 in vs model 19",
    "LOM00011930": "Lomnicky stit summit: r 0.30/0.30/-0.02, truth 48 in vs model 20",
    "HRM00014235": "Puntijarka: r -0.01/0.01/0.37, truth 71 in vs model 17",
}
FEATURED_LIFTS = 10
BELOW_BASE_M = 150
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]
COUNTRY = {"NO": "Norway", "ES": "Spain", "AD": "Andorra", "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia",
           "SI": "Slovenia", "RO": "Romania", "BG": "Bulgaria", "RS": "Serbia", "BA": "Bosnia and Herzegovina",
           "HR": "Croatia", "MK": "North Macedonia", "ME": "Montenegro", "UA": "Ukraine", "TR": "Turkey",
           "KZ": "Kazakhstan", "KG": "Kyrgyzstan", "UZ": "Uzbekistan", "CN": "China", "KR": "South Korea",
           "GE": "Georgia", "AM": "Armenia", "AZ": "Azerbaijan", "IS": "Iceland", "GB": "United Kingdom",
           "RU": "Russia", "GR": "Greece", "HU": "Hungary", "IR": "Iran", "LB": "Lebanon", "IN": "India",
           "PK": "Pakistan", "MA": "Morocco", "PT": "Portugal", "NZ": "New Zealand", "AU": "Australia",
           "CL": "Chile", "AR": "Argentina", "BY": "Belarus", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania"}


def candidates(list_path: str | None) -> list[dict]:
    st = pd.read_csv(list_path or LIST_URL, dtype=str)
    out = []
    for _, r in st.iterrows():
        iso = r["ISO_CODE"]
        if not isinstance(iso, str) or iso in COVERED:
            continue
        try:
            lat, lon, alt = float(r["LATITUDE"]), float(r["LONGITUDE"]), float(r["ELEVATION"])
        except (TypeError, ValueError):
            continue
        if lat < 0 or (alt < MIN_ALT_M and lat < 60):  # northern winters only (the south has era5/resorts)
            continue
        area, d = skiareas.nearest(lat, lon, NEAR_LIFTS)
        if area is None or d > NEAR_KM:
            continue
        out.append({"id": r["GHCN_ID"], "name": str(r["NAME"]).strip().title(), "iso": iso, "lat": lat,
                    "lon": lon, "alt": alt, "area": area["name"], "km": d})
    return out


def live_share(ghcnh, gid: str, hour: int | None, today: date) -> float:
    """Share of the LIVE_DAYS days ending LAG_DAYS ago with a report at the morning hour."""
    from datetime import timedelta

    if hour is None:
        return 0.0
    end = today - timedelta(days=LAG_DAYS)
    begin = end - timedelta(days=LIVE_DAYS - 1)
    rep = ghcnh.reports(gid, begin, end)
    got = set(rep.loc[rep["time"].dt.hour == hour, "time"].dt.date)
    return sum(begin + timedelta(days=i) in got for i in range(LIVE_DAYS)) / LIVE_DAYS


def clim_winters(ghcnh, sid: str, lon: float) -> int:
    """Winters (Nov-Mar) with >= 100 valid days inside the climatology window."""
    from powderbench.truth_sources import _CLIM_END

    t = ghcnh.daily([sid], date(2000, 10, 1), _CLIM_END, {sid: lon})
    d = pd.to_datetime(t.loc[t["valid"], "date"])
    d = d[d.dt.month.isin([11, 12, 1, 2, 3])]
    return int(((d.dt.year - (d.dt.month < 11)).value_counts() >= 100).sum())


def main() -> int:
    from powderbench.networks import ghcnh

    ap = argparse.ArgumentParser()
    ap.add_argument("--list", help="a local copy of ghcnh-station-list.csv")
    args = ap.parse_args()
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    rows = []
    for s in candidates(args.list):
        sid = f"{s['id']}:GHCNH"
        t = ghcnh.daily([sid], b, e, {sid: s["lon"]})
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        rep = ghcnh.reports(s["id"], date(last - 1, 12, 1), date(last, 2, 28))
        hour = ghcnh.morning_hour(rep.loc[rep["depth_cm"].notna(), "time"], s["lon"]) if len(rep) else None
        raw = rep[rep["time"].dt.hour == hour].dropna(subset=["depth_cm"]) if hour is not None else rep.iloc[:0]
        raw = pd.Series(raw["depth_cm"].to_numpy(float), index=raw["time"].dt.date.to_numpy())
        drops = int(ghcnh.dropouts(raw).sum()) if len(raw) else 0
        md = ghcnh.morning_depths(rep, s["lon"])
        cover = float((md > 0).mean()) if len(md) else 0.0
        plausible = (s["alt"] < HIGH_M or cover >= MIN_COVER) and drops <= MAX_DROPOUTS
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and plausible
        live = live_share(ghcnh, s["id"], hour, today) if keep else None
        keep = keep and live >= MIN_LIVE_SHARE
        winters = clim_winters(ghcnh, sid, s["lon"]) if keep else None
        keep = keep and winters >= MIN_CLIM_WINTERS
        if keep and s["id"] in NWP_GATE_FAILED:
            print(f"{sid:>18} failed the NWP gate ({NWP_GATE_FAILED[s['id']]}) -> drop", flush=True)
            keep = False
        print(f"{sid:>18} {s['iso']} {s['name'][:24]:<24} {s['alt']:6.0f} m hour={hour} days={ok_days} "
              f"snow_days={snow_days} total={t['snow24'].sum():5.1f} in cover={cover:.2f} dropouts={drops} "
              f"near={s['area'][:28]} ({s['km']:.1f} km) live={live if live is None else round(live, 2)} winters={winters} -> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            rows.append({"station_id": sid, "slug": f"ghcnh-{s['id'].lower()}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=NEAR_KM,
                                                  min_lifts=NEAR_LIFTS),
                         "region": COUNTRY.get(s["iso"], s["iso"]), "state": s["iso"],
                         "elevation_ft": round(s["alt"] * 3.28084), "latitude": round(s["lat"], 5),
                         "longitude": round(s["lon"], 5), "league": "world", "tier": "network",
                         "truth_source": "ghcnh", "network": "WMO SYNOP (GHCNh)", "external_id": s["id"],
                         "day_offset": 0, "_lat": s["lat"], "_lon": s["lon"]})
    # featured: one per big ski area, its nearest kept station
    nearest: dict[str, tuple[float, str]] = {}
    for r in rows:
        area, d = skiareas.nearest(r["_lat"], r["_lon"], FEATURED_LIFTS)
        base = float(area["min_elevation_m"] or 0) if area is not None else 0.0
        if area is not None and r["elevation_ft"] / 3.28084 < base - BELOW_BASE_M:
            continue
        if area is not None and d <= FEATURED_KM and (area["id"] not in nearest or d < nearest[area["id"]][0]):
            nearest[area["id"]] = (d, r["station_id"], area["name"])
    chosen = {v[1]: v[2] for v in nearest.values()}
    for r in rows:
        if r["station_id"] in chosen:
            r["tier"] = "featured"
            r["resort"] = chosen[r["station_id"]]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["state"], r["name"])))
    print(f"-> {len(rows)} kept ({sum(r['tier'] == 'featured' for r in rows)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate data/stations/eccc.csv: Canadian mountain stations that measure
new snowfall (ECCC climate-daily TOTAL_SNOW) for the `observers` league.

Selection:
  - daily data still flowing (DLY_LAST_DATE within ~2 months)
  - mountains: BC/YT >= 900 m; Alberta >= 900 m *west of 114.3W* (Rockies and
    foothills, not prairie airports); eastern Canada >= 500 m (as for US)
  - >= MIN_REPORTS TOTAL_SNOW reports last winter (most automatic stations
    don't measure snowfall at all; some report a constant 0)
  - a clear observation-day fit against ERA5 (networks/attribution.py)

Featured: the staffed ski-hill and mountain sites (FEATURED).

    python scripts/networks/eccc.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "data" / "stations" / "eccc.csv"
STATIONS_URL = "https://api.weather.gc.ca/collections/climate-stations/items"
MIN_REPORTS = 100
FEATURED = {"1108906", "1128584", "1105658", "1090660", "3053600"}  # Whistler Roundhouse, Silver Star, Grouse, Barkerville, Kananaskis
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]
RESORTS = {"1108906": "Whistler Blackcomb", "1128584": "SilverStar", "1105658": "Grouse Mountain"}


def dms(v: int) -> float:
    """ECCC packs coordinates as DD.MMSSss x 1e7 (e.g. 500404000 = 50°04'04"),
    not decimal degrees."""
    x = abs(v) / 1e7
    deg = int(x)
    mmss = round((x - deg) * 1e4, 4)  # MMSS.ss
    minutes, seconds = int(mmss // 100), mmss % 100
    out = deg + minutes / 60 + seconds / 3600
    return -out if v < 0 else out


def region(prov: str, lat: float, lon: float) -> str:
    if prov == "BC":
        if lat >= 51.5:
            return "Cariboo, BC"
        return "Coast Mountains, BC" if lon < -122.3 else "Interior BC"
    if prov == "AB":
        return "Canadian Rockies, AB"
    if prov == "YT":
        return "Yukon"
    return {"QC": "Quebec", "ON": "Ontario"}.get(prov, "Atlantic Canada")


def main() -> int:
    import pandas as pd

    from powderbench.networks import attribution, eccc
    from powderbench.openmeteo import era5_daily_snowfall
    from powderbench.stations import Station

    feats = requests.get(STATIONS_URL, params={"f": "json", "limit": 10000}, timeout=300).json()["features"]
    recent = (date.today() - timedelta(days=60)).isoformat()
    cands = []
    for f in feats:
        p = f["properties"]
        if (p.get("DLY_LAST_DATE") or "") < recent:
            continue
        try:
            elev = float(p["ELEVATION"])
        except (TypeError, ValueError):
            continue
        prov, lat, lon = p["PROV_STATE_TERR_CODE"], dms(p["LATITUDE"]), dms(p["LONGITUDE"])
        mountain = ((prov in ("BC", "YT") and elev >= 900) or (prov == "AB" and elev >= 900 and lon < -114.3)
                    or (prov in ("QC", "ON", "NB", "NS", "NL") and elev >= 500))
        if mountain:
            cands.append((p, lat, lon, elev))

    rows = []
    for p, lat, lon, elev in cands:
        cid, prov = p["CLIMATE_IDENTIFIER"], p["PROV_STATE_TERR_CODE"]
        name = p["STATION_NAME"].title()
        rows.append({
            "station_id": f"{cid}:{prov}:ECCC", "slug": f"eccc-{cid.lower()}", "name": name,
            "resort": RESORTS.get(cid, name), "region": region(prov, lat, lon), "state": prov,
            "elevation_ft": round(elev * 3.28084), "latitude": round(lat, 5), "longitude": round(lon, 5),
            "league": "observers", "tier": "featured" if cid in FEATURED else "network",
            "truth_source": "eccc", "network": "ECCC", "external_id": cid,
        })

    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    sts = [Station(**{k: r[k] for k in ("station_id", "slug", "name", "resort", "region", "state",
                                        "elevation_ft", "latitude", "longitude")}) for r in rows]
    reps, mods = [], []
    for b, e in attribution.winters(last):
        raw = eccc.fetch_snow([s.station_id for s in sts], b, e)
        reps.append(raw[~raw["flag"].isin(eccc.VOID_FLAGS)])
        mods.append(era5_daily_snowfall(sts, b, e))
    reports = pd.concat(reps)
    n_last = reports[reports["date"] >= attribution.winters(last)[-1][0]].groupby("station_id").size()
    fits = attribution.fit_offsets(reports, pd.concat(mods))

    kept = []
    for r in rows:
        fit = fits.get(r["station_id"], {})
        n = int(n_last.get(r["station_id"], 0))
        verdict = "ok" if n >= MIN_REPORTS and fit.get("offset") is not None else "dropped"
        print(f"{r['station_id']:>18} {r['name'][:28]:<28} reports={n:<4} corr={fit.get('corr')} -> {verdict}")
        if verdict == "ok":
            r["day_offset"] = fit["offset"]
            kept.append(r)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: (r["state"], r["name"])))
    print(f"{len(cands)} mountain candidates -> {len(kept)} kept "
          f"({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

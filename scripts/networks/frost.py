"""Generate data/stations/frost.csv: MET Norway stations with a daily 06 UTC
snow-depth reading (Frost), for the nordic league.

Candidates: Norwegian stations (not Svalbard / Jan Mayen) with an active
`surface_snow_thickness` P1D series, within NEAR_KM of a ski area or at
>= MIN_ALT_M. A candidate is kept when:
  - last winter (Nov-Mar) has >= MIN_DAYS valid days and >= MIN_SNOW_DAYS days
    of 2 cm or more (the shared nordic gate);
  - the series started by CLIM_FROM, so the climatology has >= 3 winters;
  - it reported on >= MIN_LIVE_SHARE of the last LIVE_DAYS days, as read now;
  - it passes the per-station NWP gate (data/gates/nordic.csv), which also
    sets its day_offset.
Featured: stations within FEATURED_KM of a ski area with >= FEATURED_LIFTS
lifts, no more than BELOW_BASE_M below its lowest lift, one per area, the
FEATURED_MAX biggest areas only (the featured set is what costs Open-Meteo
calls, and its budget does not rise).

Needs FROST_CLIENT_ID. Elevations are Frost's own station metadata.

    FROST_CLIENT_ID=... python scripts/networks/frost.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "frost.csv"
SERIES = "https://frost.met.no/observations/availableTimeSeries/v0.jsonld"
SOURCES = "https://frost.met.no/sources/v0.jsonld"
NEAR_KM = 25.0
MIN_ALT_M = 400
MIN_DAYS = 120
MIN_SNOW_DAYS = 10
CLIM_FROM = date(2022, 10, 1)
LIVE_DAYS = 14
MIN_LIVE_SHARE = 0.7
FEATURED_KM = 12.0
FEATURED_LIFTS = 3
BELOW_BASE_M = 150
FEATURED_MAX = 3
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def stations() -> list[dict]:
    from powderbench.networks import frost

    auth = (frost.client_id(), "")
    series = requests.get(SERIES, params={"elements": frost.DEPTH, "timeresolutions": "P1D", "timeoffsets": "PT6H"},
                          auth=auth, headers=frost.UA, timeout=120).json()["data"]
    since: dict[str, date] = {}
    for s in series:
        if s.get("validTo"):
            continue
        sn = s["sourceId"].split(":")[0]
        start = datetime.fromisoformat(s["validFrom"].replace("Z", "+00:00")).date()
        since[sn] = min(since.get(sn, start), start)
    ids = sorted(since)
    meta = []
    for i in range(0, len(ids), 100):
        meta += requests.get(SOURCES, params={"ids": ",".join(ids[i : i + 100]),
                                              "fields": "id,name,masl,geometry,country,county"},
                             auth=auth, headers=frost.UA, timeout=120).json()["data"]
    out = []
    for m in meta:
        if m.get("country") != "Norge" or "geometry" not in m:
            continue
        lon, lat = m["geometry"]["coordinates"]
        out.append({"id": m["id"], "name": m["name"].title(), "lat": float(lat), "lon": float(lon),
                    "alt": float(m.get("masl") or 0), "county": (m.get("county") or "").title(),
                    "since": since[m["id"]]})
    return out


def main() -> int:
    from powderbench.networks import frost

    today = datetime.now(timezone.utc).date()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = date(last - 1, 11, 1), date(last, 3, 31)
    cands = []
    for s in stations():
        area, d = skiareas.nearest(s["lat"], s["lon"], 1)
        if s["alt"] >= MIN_ALT_M or (area is not None and d <= NEAR_KM):
            cands.append(s)
    rows = []
    for s in cands:
        sid = f"{s['id']}:NO:FROST"
        t = frost.daily([sid], b, e)
        ok_days = int(t["valid"].sum())
        snow_days = int((t["snow24"] >= 2 / 2.54).sum())
        drift = int((t["qc_flag"] == "no_swe_support").sum())
        keep = ok_days >= MIN_DAYS and snow_days >= MIN_SNOW_DAYS and s["since"] <= CLIM_FROM
        live = None
        if keep:
            rec = frost.record(s["id"], today - timedelta(days=LIVE_DAYS), today)["depth_cm"].dropna()
            recent = [d for d in rec.index if today - timedelta(days=LIVE_DAYS) <= d < today]
            live = len(recent) / LIVE_DAYS
            keep = live >= MIN_LIVE_SHARE
        offset, why = gate.decide("nordic", sid, 1) if keep else (None, "")
        keep = keep and offset is not None
        print(f"{sid:>16} {s['name'][:26]:<26} {s['alt']:5.0f} m since={s['since']} days={ok_days} "
              f"snow_days={snow_days} drift={drift} live={live if live is None else round(live, 2)} "
              f"{why} -> {'keep' if keep else 'drop'}", flush=True)
        if keep:
            rows.append({"station_id": sid, "slug": f"frost-{s['id'].lower()}", "name": s["name"],
                         "resort": skiareas.label(s["name"], s["lat"], s["lon"], within_km=FEATURED_KM,
                                                  min_lifts=FEATURED_LIFTS),
                         "region": "Norway", "state": "NO", "elevation_ft": round(s["alt"] * 3.28084),
                         "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5), "league": "nordic",
                         "tier": "network", "truth_source": "frost", "network": "MET Norway",
                         "external_id": s["id"], "day_offset": offset, "_s": s})
    # featured: nearest kept station of each big area, the FEATURED_MAX biggest areas
    nearest: dict[str, tuple[float, str, int, str]] = {}
    for r in rows:
        s = r["_s"]
        area, d = skiareas.nearest(s["lat"], s["lon"], FEATURED_LIFTS)
        if area is None or d > FEATURED_KM:
            continue
        if s["alt"] < float(area.get("min_elevation_m") or 0) - BELOW_BASE_M:
            continue
        if area["id"] not in nearest or d < nearest[area["id"]][0]:
            nearest[area["id"]] = (d, r["station_id"], area["lift_count"], area["name"])
    top = sorted(nearest.values(), key=lambda v: -v[2])[:FEATURED_MAX]
    chosen = {v[1]: v[3] for v in top}
    for r in rows:
        if r["station_id"] in chosen:
            r["tier"] = "featured"
        r.pop("_s")
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["name"]))
    print(f"{len(cands)} candidates -> {len(rows)} kept; featured: "
          + ", ".join(f"{v[3]} ({v[2]} lifts, {v[0]:.1f} km)" for v in top) + f" -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

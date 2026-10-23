"""Generate data/stations/nivo.csv: Météo-France nivo-meteorological stations
(observers at French ski resorts) in the French Alps, for the alps league.

Candidates: stations in the Météo-France list east of 5°E / north of 43.7°N
(the Alps; the Pyrenees and Corsica don't belong in this league) with at
least MIN_REPORTS morning new-snow readings last winter. Day attribution is
fitted per station against ERA5: the morning-board protocol (+1) unless 0
wins clearly.

    python scripts/networks/nivo.py
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "nivo.csv"
POSTES = ("https://static.data.gouv.fr/resources/archive-reseau-nivo-meteorologique/"
          "20260422-141208/observations-liste-postes-nivo-20250710.csv")
MIN_REPORTS = 60
ALPS_MIN_LIFTS = 4
FEATURED = {  # WMO id -> resort
    "07393": "Chamonix – Le Tour", "07470": "Chamonix – La Flégère", "07858": "Avoriaz", "07856": "Flaine",
    "07901": "La Rosière", "07900": "Les Arcs", "07895": "La Plagne", "07904": "Tignes",
    "07498": "Val d'Isère", "07893": "Courchevel", "07892": "Méribel", "07918": "Val Thorens",
    "07811": "Alpe d'Huez", "07881": "Les 2 Alpes", "07926": "Serre Chevalier",
}
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]


def _title(name: str) -> str:
    name = name.replace("_NIVO2", "").replace("_NIVO", "").replace(" NIVO", "").replace("-NIVO", "").strip()
    return name.title() if name.isupper() else name


def main() -> int:
    import pandas as pd

    from powderbench.networks import attribution, nivo
    from powderbench.openmeteo import era5_daily_snowfall
    from powderbench.stations import Station

    postes = pd.read_csv(io.StringIO(requests.get(POSTES, timeout=120).text), dtype={"ID": str})
    postes["ID"] = postes["ID"].str.zfill(5)
    alps = postes[(postes["Longitude"] > 5.0) & (postes["Latitude"] > 43.7)]
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    b, e = attribution.winters(last)[-1]
    ids = [f"{i}:FR:NIVO" for i in alps["ID"]]
    n_last = nivo.fetch_snow(ids, b, e).dropna().groupby("station_id").size()

    rows = []
    for p in alps.itertuples():
        sid = f"{p.ID}:FR:NIVO"
        if n_last.get(sid, 0) < MIN_REPORTS:
            continue
        name = _title(p.Nom)
        rows.append({"station_id": sid, "slug": f"nivo-{p.ID}", "name": name,
                     "resort": FEATURED.get(p.ID) or skiareas.label(name, p.Latitude, p.Longitude,
                                                                    min_lifts=ALPS_MIN_LIFTS),
                     "region": "Northern French Alps" if p.Latitude >= 45.0 else "Southern French Alps",
                     "state": "FR", "elevation_ft": round(p.Altitude * 3.28084),
                     "latitude": round(p.Latitude, 5), "longitude": round(p.Longitude, 5), "league": "alps",
                     "tier": "featured" if p.ID in FEATURED else "network", "truth_source": "nivo",
                     "network": "Météo-France nivo", "external_id": p.ID})

    sts = [Station(**{k: r[k] for k in ("station_id", "slug", "name", "resort", "region", "state",
                                        "elevation_ft", "latitude", "longitude")}) for r in rows]
    reps, mods = [], []
    for wb, we in attribution.winters(last):
        reps.append(nivo.fetch_snow([s.station_id for s in sts], wb, we).dropna())
        mods.append(era5_daily_snowfall(sts, wb, we))
    # morning board, mostly yesterday's snow (+1) by protocol; a clear win for
    # 0 (overnight storms; most automated *_NIVO sites) overrides it
    fits = attribution.fit_offsets(pd.concat(reps), pd.concat(mods), allowed=(0, 1),
                                   expected={r["station_id"]: 1 for r in rows})
    kept = []
    for r in rows:
        fit = fits.get(r["station_id"], {})
        off = fit.get("offset")
        print(f"{r['station_id']:>12} {r['name']:<28} {r['tier']:<8} corr={fit.get('corr')} -> {off}")
        if off is not None:
            r["day_offset"] = off
            kept.append(r)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: r["name"]))
    print(f"{len(alps)} Alps stations -> {len(rows)} with records -> {len(kept)} kept "
          f"({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

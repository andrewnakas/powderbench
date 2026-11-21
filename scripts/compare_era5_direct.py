"""Direct ERA5 (open-data archive or CDS) vs the era5 league's published truth (Open-Meteo
era5_seamless), day by day: does the direct read reproduce it?

  PYTHONPATH=src python scripts/compare_era5_direct.py [s3|era5|era5-land] [first] [last]

s3 = the open-data archive; era5 / era5-land = the Copernicus CDS.
"""

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from powderbench.nwp import era5
from powderbench.stations import data_dir, load_stations

product = sys.argv[1] if len(sys.argv) > 1 else "era5"
rounds = sorted(p.name for p in (data_dir() / "rounds" / "era5").iterdir() if (p / "truth.csv").exists())
first = date.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else rounds[0])
last = date.fromisoformat(sys.argv[3] if len(sys.argv) > 3 else rounds[-1])

rows = []
for r in rounds:
    d = date.fromisoformat(r)
    if first <= d <= last:
        t = pd.read_csv(data_dir() / "rounds" / "era5" / r / "truth.csv")
        t = t[(t["horizon_h"] == 24) & t["valid"]]
        rows += [{"station_id": s, "date": d, "om_in": v} for s, v in zip(t["station_id"], t["truth_in"])]
om = pd.DataFrame(rows)
stations = [s for s in load_stations("era5") if s.station_id in set(om["station_id"])]
direct = (era5.daily_snowfall(stations, first, last, "era5", source="s3") if product == "s3"
          else era5.daily_snowfall(stations, first, last, product, source="cds")).rename(columns={"snowfall_in": "direct_in"})
m = om.merge(direct, on=["station_id", "date"], how="inner")
d = m["direct_in"] - m["om_in"]
wet = (m["om_in"] > 0.05) | (m["direct_in"] > 0.05)
print(f"{product}: {len(m)} station-days ({m['station_id'].nunique()} stations, {first}..{last})")
print(f"  totals: open-meteo {m['om_in'].sum():.1f} in, direct {m['direct_in'].sum():.1f} in "
      f"(ratio {m['direct_in'].sum() / max(m['om_in'].sum(), 1e-9):.3f})")
print(f"  MAE {d.abs().mean():.4f} in, on snow days {d[wet].abs().mean():.4f} in; "
      f"corr {np.corrcoef(m['om_in'], m['direct_in'])[0, 1]:.4f}")
print(f"  within 0.01 in: {(d.abs() <= 0.01).mean():.1%}; within 10% on snow days: "
      f"{((d.abs() <= 0.1 * m[['om_in', 'direct_in']].max(axis=1)) & wet).sum() / max(wet.sum(), 1):.1%}")
per = m.groupby("station_id")[["om_in", "direct_in"]].sum()
per["ratio"] = per["direct_in"] / per["om_in"].where(per["om_in"] > 0)
print(per.round(2).sort_values("ratio").to_string())
# a one-day shift would show here as a better lagged match
for lag in (-1, 1):
    sh = direct.assign(date=direct["date"] + timedelta(days=lag))
    mm = om.merge(sh, on=["station_id", "date"])
    print(f"  lag {lag:+d} day: MAE {(mm['direct_in'] - mm['om_in']).abs().mean():.4f}")

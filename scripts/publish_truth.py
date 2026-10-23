"""Publish PowderBench's QC'd daily snowfall truth to Hugging Face
(nakas/powderbench-truth): the exact station-day values the benchmark scores
against, for every station-network league, with QC flags — a ready-made
training/verification set.

One config per league (Parquet: station_id, date, snow24_in, valid, qc_flag)
plus station metadata. Licences follow the sources (see the card).

    python scripts/publish_truth.py --out _truth [--league stations] [--upload]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REPO = "nakas/powderbench-truth"
LEAGUES = {  # league -> (history start, source/licence line)
    "stations": (date(2015, 10, 1), "NRCS SNOTEL and SNOLITE (USDA) — US public domain; California DWR CDEC — "
                                     "public; BC Ministry of Environment snow pillows — Open Government Licence – BC"),
    "observers": (date(2016, 10, 1), "NOAA GHCN-Daily (NWS COOP: public domain; CoCoRaHS: CC BY 3.0, © CoCoRaHS) "
                                     "and Environment and Climate Change Canada (Open Government Licence – Canada)"),
    "alps": (date(2000, 10, 1), "WSL Institute for Snow and Avalanche Research SLF — CC BY 4.0, www.slf.ch; "
                                 "GeoSphere Austria — CC BY 4.0, data.hub.geosphere.at"),
}


def export(league: str, out: Path, fresh: bool = False) -> dict:
    import pandas as pd

    from powderbench.leagues import get_league
    from powderbench.stations import load_stations
    from powderbench.truth_sources import daily_truth

    lg = get_league(league)
    start, _ = LEAGUES[league]
    end = date.today() - timedelta(days=lg.resolve_after_days + 1)
    sts = load_stations(league)
    ids = [s.station_id for s in sts]
    folder = out / league
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    cur = start
    while cur <= end:
        # calendar-year chunks: the same windows the truth fetchers cache, so a
        # re-export is (almost) all cache hits
        stop = min(date(cur.year, 12, 31), end)
        path = folder / f"truth_{cur:%Y%m%d}_{stop:%Y%m%d}.parquet"
        if fresh or not path.exists() or stop >= end - timedelta(days=40):  # recent chunks get refreshed
            frames = [daily_truth(lg, ids[i : i + 200], cur, stop) for i in range(0, len(ids), 200)]
            df = pd.concat(frames, ignore_index=True).rename(columns={"snow24": "snow24_in"})
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values(["station_id", "date"])
            df.to_parquet(path, index=False)
        n += len(pd.read_parquet(path, columns=["valid"]))
        print(f"[{league}] {path.name}", flush=True)
        cur = stop + timedelta(days=1)
    meta = pd.DataFrame([{k: v for k, v in asdict(s).items()} for s in sts])
    meta.to_parquet(folder / "stations.parquet", index=False)
    return {"league": league, "rows": n, "stations": len(sts), "start": start.isoformat(), "end": end.isoformat()}


def card(stats: list[dict]) -> str:
    configs = []
    for s in stats:
        configs += [f"- config_name: {s['league']}", f"  data_files: \"{s['league']}/truth_*.parquet\"",
                    f"- config_name: {s['league']}_stations", f"  data_files: \"{s['league']}/stations.parquet\""]
    rows = "\n".join(
        f"| `{s['league']}` | {s['stations']:,} | {s['start']} → {s['end']} | {s['rows']:,} | {LEAGUES[s['league']][1]} |"
        for s in stats)
    return f"""---
license: other
pretty_name: PowderBench daily snowfall truth
tags: [snow, snowfall, weather, forecasting, benchmark, snotel, ghcn]
task_categories: [time-series-forecasting, tabular-regression]
configs:
{chr(10).join(configs)}
---

# PowderBench daily snowfall truth

The quality-controlled daily new-snow values that
[PowderBench](https://powderbench.com) — the live mountain-snowfall forecasting
benchmark — scores forecasts against, for every station-network league.
Train on it, verify against it, then go beat the weather models live.

| League | Stations | Days | Station-days | Source & licence |
|---|---|---|---|---|
{rows}

Columns: `station_id`, `date` (station-local day), `snow24_in` (new snow,
inches; NaN when voided), `valid`, `qc_flag` (`ok`, `missing`,
`implausible_jump`, `no_swe_support`, `source_qc_failed`, …). Station metadata
(coordinates, elevation, region, featured/network tier, fitted observation-day
offset) in each `<league>_stations` config.

How the truth is built (all code in the [repo](https://github.com/andrewnakas/powderbench)):
- **stations** — snow-depth deltas at SNOTEL, SNOLITE, CDEC and BC snow
  pillows, voided on missing/absurd depths, >48" jumps, big jumps without an
  SWE increase (or a neighbour's, for SNOLITE), implausible density, stuck
  sensors and spikes (CDEC uses the hourly reading at local midnight).
- **observers** — *measured* new snow from observers' snowboards; each
  station's observation-day offset is fitted against ERA5 and ambiguous
  stations are excluded (docs/DATA.md).
- **alps** — SLF's measured 24h new snow (HN) at manual study plots and IMIS
  stations, same per-station attribution.

Every source keeps its licence: cite the original providers listed above when
you use the data. Suggested splits are by season (never random — neighbouring
days are strongly correlated).
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("_truth"))
    ap.add_argument("--league", action="append")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="rebuild every chunk (after station/QC changes)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    statf = args.out / "stats.json"
    stats = json.loads(statf.read_text()) if statf.exists() else {}
    for lg in args.league or LEAGUES:
        stats[lg] = export(lg, args.out, args.fresh)
    statf.write_text(json.dumps(stats, indent=1))
    (args.out / "README.md").write_text(card([stats[k] for k in LEAGUES if k in stats]))
    if args.upload:
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(REPO, repo_type="dataset", exist_ok=True)
        api.upload_folder(repo_id=REPO, repo_type="dataset", folder_path=str(args.out),
                          ignore_patterns=["stats.json"], commit_message="PowderBench truth export")
        print(f"uploaded -> https://huggingface.co/datasets/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

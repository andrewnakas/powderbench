"""One-off (2026-11-13): apply the stuck-report QC to resort rounds that had
already resolved.

Narrow by design. For each resolved round of the resort leagues:
- a truth row is voided when its window contains a resort-day the QC now
  drops (a repeat of a stuck report); no other truth row changes;
- every team that was scored is re-scored against the patched truth;
- late / invalid flags, reference observations and the original resolution
  time are kept exactly as published.

    python scripts/rescore_stuck_resort_rounds.py
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from powderbench import climatology, scoring
from powderbench.leagues import get_league
from powderbench.resortfeeds import archive
from powderbench.rounds import _biggest_24h, results_dir, round_dir, round_station_ids, submissions_dir
from powderbench.stations import by_id

LEAGUES = ("resorts", "resorts-na")


def stuck_days() -> set[tuple[str, date]]:
    begin, end = date(2026, 6, 1), date.today()
    kept = archive.daily_from_archive(begin, end)
    qc, archive._drop_stuck = archive._drop_stuck, lambda d: d
    try:
        everything = archive.daily_from_archive(begin, end)
    finally:
        archive._drop_stuck = qc
    have = set(zip(kept["station_id"], kept["date"]))
    return {k for k in zip(everything["station_id"], everything["date"]) if k not in have}


def main() -> int:
    stuck = stuck_days()
    rounds = rows = 0
    for name in LEAGUES:
        league = get_league(name)
        registry = by_id(name)
        for path in sorted(results_dir(league).glob("*.json")):
            payload = json.loads(path.read_text())
            d = date.fromisoformat(payload["round_id"])
            tp = round_dir(league, d) / "truth.csv"
            truth = pd.read_csv(tp)
            truth["valid"] = truth["valid"].astype(bool)
            hit = truth["valid"] & truth.apply(
                lambda r: any((r["station_id"], d + timedelta(days=i)) in stuck for i in range(int(r["horizon_h"]) // 24)),
                axis=1)
            if not hit.any():
                continue
            truth.loc[hit, "valid"] = False
            truth.to_csv(tp, index=False)
            stations = [registry[s] for s in round_station_ids(league, d) if s in registry]
            climo = climatology.climatology_prediction(d, league)
            for team, old in payload["teams"].items():
                sub = submissions_dir(league, d) / f"{team}.csv"
                if old.get("invalid") or not sub.exists():
                    continue
                new = scoring.score_tracks(pd.read_csv(sub), truth, climo, stations)
                new["late"] = old.get("late", False)
                payload["teams"][team] = new
            payload["qc"] = {"station_horizons_valid": int(truth["valid"].sum()),
                             "station_horizons_voided": int((~truth["valid"]).sum())}
            payload["biggest_24h"] = _biggest_24h(truth, league)
            payload["rescored_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            payload["rescored_reason"] = f"stuck-report QC: {int(hit.sum())} station-horizons voided (repeated resort figures)"
            path.write_text(json.dumps(payload, indent=1))
            rounds += 1
            rows += int(hit.sum())
    print(f"re-scored {rounds} rounds; voided {rows} station-horizons")
    return 0


if __name__ == "__main__":
    sys.exit(main())

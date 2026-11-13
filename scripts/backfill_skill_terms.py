"""One-off: add mae_skill / mae_climo (and n_skill on tracks) to round results
resolved before scoring recorded them, so the leaderboard can pool rounds.

Re-scores each stored submission against the round's stored truth.csv and the
league climatology. Nothing else in a result file changes. Where the
re-scored Powder Accuracy differs from the published one (the climatology was
rebuilt since), the published score stands and mae_climo is the value it implies.

    python scripts/backfill_skill_terms.py            # all leagues; safe to rerun (resolve.yml does, daily)
"""

import json
import sys
from datetime import date

import pandas as pd

from powderbench import climatology, scoring
from powderbench.leagues import load_leagues
from powderbench.rounds import results_dir, round_dir, round_station_ids, submissions_dir
from powderbench.stations import by_id

NEW = ("n_skill", "mae_skill", "mae_climo")


def main() -> int:
    done = skipped = 0
    for league in load_leagues():
        registry = by_id(league.name)
        for path in sorted(results_dir(league).glob("*.json")):
            payload = json.loads(path.read_text())
            d = date.fromisoformat(payload["round_id"])
            tp = round_dir(league, d) / "truth.csv"
            todo = [t for t, m in payload["teams"].items()
                    if not m.get("invalid") and m.get("powder_score") is not None and "mae_climo" not in m]
            if not todo or not tp.exists():
                continue  # nothing to add: the usual case, so reruns are instant
            truth_d = pd.read_csv(tp)
            truth_d["valid"] = truth_d["valid"].astype(bool)
            stations = [registry[s] for s in round_station_ids(league, d) if s in registry]
            climo = climatology.climatology_prediction(d, league)
            changed = False
            for team, m in payload["teams"].items():
                sub = submissions_dir(league, d) / f"{team}.csv"
                if m.get("invalid") or m.get("powder_score") is None or "mae_climo" in m or not sub.exists():
                    continue
                new = scoring.score_tracks(pd.read_csv(sub), truth_d, climo, stations)
                if new.get("powder_score") is None:
                    skipped += 1
                    continue
                m.update({k: new[k] for k in NEW if k in new})
                if abs(new["powder_score"] - m["powder_score"]) > max(0.05, 1e-3 * abs(m["powder_score"])):
                    # the climatology was rebuilt after this round resolved:
                    # keep the published score and the team's own error, and
                    # record the climatology error that score implies
                    if m["powder_score"] >= 100:
                        skipped += 1
                        continue
                    m["mae_climo"] = round(new["mae_skill"] / (1 - m["powder_score"] / 100), 5)
                    print(f"  kept published score: {league.name}/{d} {team}: {m['powder_score']} (re-scored {new['powder_score']})")
                    done += 1
                    changed = True
                    continue
                for name, t in (m.get("tracks") or {}).items():
                    nt = (new.get("tracks") or {}).get(name) or {}
                    t.update({k: nt[k] for k in NEW if nt.get(k) is not None})
                done += 1
                changed = True
            if changed:
                path.write_text(json.dumps(payload, indent=1))
    print(f"backfilled {done} team-rounds, left {skipped} untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

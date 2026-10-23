"""The forecasting briefing: everything an LLM agent (or a human) needs to
forecast one open round, in one compact document.

    powderbench brief --league stations            # Markdown, for an LLM
    powderbench brief --league stations --json     # the same, structured

Contents per station: location/elevation/region, every model's forecast for
24/48/72h (from the round's baseline submissions, written 2.5-9 h before
the cutoff), the house agents' calls, climatology (median and powder-day odds),
the last week of observed snow, and each model's recent skill in the league.
Plus the rules of the round and the exact output format.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .. import climatology
from ..leagues import League
from ..rounds import round_dir, round_station_ids, submissions_dir
from ..stations import by_id
from .blend import NON_MODELS, history

SKILL_WINDOW_ROUNDS = 30


def _submissions(league: League, d: date, prefix: str) -> pd.DataFrame:
    frames = []
    for p in sorted(submissions_dir(league, d).glob(f"{prefix}*.csv")):
        if p.stem in NON_MODELS:
            continue
        df = pd.read_csv(p, usecols=lambda c: c in ("station_id", "horizon_h", "snowfall_in"))
        frames.append(df.assign(team=p.stem.removeprefix("baseline-")))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["station_id", "horizon_h", "snowfall_in", "team"])


def _recent_snow(league: League, d: date, ids: set[str]) -> dict[str, list]:
    """Observed 24h snow over the last ~week from resolved rounds' truth."""
    out: dict[str, dict[str, float]] = {}
    for k in range(4, 14):
        rd = round_dir(league, d - timedelta(days=k)) / "truth.csv"
        if not rd.exists():
            continue
        t = pd.read_csv(rd)
        t = t[(t["horizon_h"] == 24) & (t["valid"].astype(str) == "True") & t["station_id"].isin(ids)]
        for r in t.itertuples():
            out.setdefault(r.station_id, {})[rd.parent.name] = round(float(r.truth_in), 1)
    return {s: sorted(v.items()) for s, v in out.items()}


def build_brief(league: League, d: date, featured_only: bool = True) -> dict:
    manifest = json.loads((round_dir(league, d) / "round.json").read_text())
    ids = manifest.get("featured") if featured_only and manifest.get("featured") else round_station_ids(league, d)
    meta = by_id(league.name)
    models = _submissions(league, d, "baseline-")
    house = _submissions(league, d, "house-")
    try:
        climo = climatology.climatology_prediction(d, league)
    except FileNotFoundError:
        climo = pd.DataFrame(columns=["station_id", "horizon_h", "snowfall_in", "prob_6in"])
    recent = _recent_snow(league, d, set(ids))

    # each model's recent skill in this league (MAE by horizon), from resolved rounds
    hist = history(league, datetime.now(timezone.utc))
    skill = {}
    if len(hist):
        recent_rounds = sorted(hist["round"].unique())[-SKILL_WINDOW_ROUNDS:]
        h = hist[hist["round"].isin(recent_rounds)]
        h = h.assign(err=(h["pred"] - h["truth_in"]).abs())
        for (m, hz), g in h.groupby(["model", "horizon_h"]):
            skill.setdefault(m.removeprefix("baseline-"), {})[int(hz)] = round(float(g["err"].mean()), 2)

    stations = []
    for sid in ids:
        s = meta.get(sid)
        entry = {"station_id": sid, "name": s.name if s else sid, "resort": s.resort if s else "",
                 "region": s.region if s else "", "lat": s.latitude if s else None,
                 "lon": s.longitude if s else None, "elevation_ft": s.elevation_ft if s else None,
                 "models": {}, "house": {}, "climatology": {}, "recent_24h_snow": recent.get(sid, [])}
        for df, key in ((models, "models"), (house, "house")):
            for r in df[df["station_id"] == sid].itertuples():
                entry[key].setdefault(r.team, {})[int(r.horizon_h)] = round(float(r.snowfall_in), 1)
        for r in climo[climo["station_id"] == sid].itertuples():
            entry["climatology"][int(r.horizon_h)] = round(float(r.snowfall_in), 1)
            if int(r.horizon_h) == 24 and pd.notna(getattr(r, "prob_6in", None)):
                entry["climatology"]["p_6in"] = round(float(r.prob_6in), 3)
        stations.append(entry)
    return {
        "league": league.name, "round": d.isoformat(), "cutoff_utc": manifest["cutoff_utc"],
        "target_days": manifest["target_days"], "truth_source": league.truth_source,
        "n_stations_in_round": len(manifest["stations"]), "stations": stations,
        "model_recent_mae_in": skill,
        "output": {
            "file": f"data/submissions/{league.name}/{d.isoformat()}/<your-team>.csv",
            "columns": "station_id,horizon_h,snowfall_in[,p10,p25,p50,p75,p90,prob_6in]",
            "horizons": "24 = day D, 48 = D..D+1, 72 = D..D+2 (cumulative inches of NEW snow, station-local days)",
            "submit": f"powderbench submit <file.csv> --team <your-team> --league {league.name} --round-date {d.isoformat()}",
        },
    }


def brief_markdown(b: dict) -> str:
    L = [f"# PowderBench forecast briefing — {b['league']} round {b['round']}",
         "",
         f"- **Cutoff:** {b['cutoff_utc']} (submissions after it are flagged late and not ranked)",
         f"- **Target days (station-local):** {', '.join(b['target_days'])}",
         f"- **Truth:** {b['truth_source']} — see https://powderbench.com/methods.html",
         f"- **Stations here:** {len(b['stations'])} featured (ranked) of {b['n_stations_in_round']} in the round",
         "",
         "## Task",
         "For every station, forecast cumulative NEW snowfall in inches for horizons 24, 48 and 72 h. "
         "Optional: quantiles p10..p90 (non-decreasing) and prob_6in (P of >= 6\" in the first 24 h). "
         "Scored by MAE relative to climatology (Powder Score), pinball loss and Brier.",
         "",
         f"Output CSV columns: `{b['output']['columns']}` — {b['output']['horizons']}.",
         f"Submit: `{b['output']['submit']}`",
         ""]
    if b["model_recent_mae_in"]:
        L += ["## Recent model skill in this league (MAE, inches, last resolved rounds)", "",
              "| model | 24h | 48h | 72h |", "|---|---|---|---|"]
        for m, v in sorted(b["model_recent_mae_in"].items(), key=lambda kv: kv[1].get(24, 9)):
            L.append(f"| {m} | {v.get(24, '—')} | {v.get(48, '—')} | {v.get(72, '—')} |")
        L.append("")
    L += ["## Stations", "",
          "Per station: model forecasts as 24/48/72 h inches; house = the house agents' calls; "
          "climo = climatological median (and P(>=6\") on day 1); recent = observed 24 h snow on recent days.", ""]
    for s in b["stations"]:
        fmt = lambda d: "/".join(str(d.get(h, "—")) for h in (24, 48, 72))
        L.append(f"### {s['resort'] or s['name']} — {s['station_id']}")
        L.append(f"{s['region']} · {s['lat']}, {s['lon']} · {int(s['elevation_ft'] or 0):,} ft")
        if s["models"]:
            L.append("- models: " + "; ".join(f"{m} {fmt(v)}" for m, v in sorted(s["models"].items())))
        if s["house"]:
            L.append("- house: " + "; ".join(f"{m} {fmt(v)}" for m, v in sorted(s["house"].items())))
        if s["climatology"]:
            c = s["climatology"]
            L.append(f"- climo: {fmt(c)}" + (f" (P6\" {c['p_6in']})" if "p_6in" in c else ""))
        if s["recent_24h_snow"]:
            L.append("- recent: " + ", ".join(f"{dd[5:]} {v}\"" for dd, v in s["recent_24h_snow"]))
        L.append("")
    return "\n".join(L)

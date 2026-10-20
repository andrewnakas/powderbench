"""Storm Mode: storm-total contests.

When a big storm is coming, a maintainer opens an *event*: a region (bounding
box) of an existing league, a window of station-local days, and a cutoff.
Everyone predicts the storm-total snowfall (inches) at each station; truth is
the league's own QC'd daily truth summed over the window (a station counts
only if every day in the window is valid — no partial totals).

    data/events/<event_id>/event.json           the manifest (frozen stations)
    data/submissions/events/<event_id>/<team>.csv   station_id, snowfall_in [, p10..p90]
    data/results/events/<event_id>.json         scores

Scoring: MAE and the Powder Score against a climatology storm total (the sum
of each day's climatological mean), plus pinball loss when quantiles are
given. Baselines: each NWP model's storm total and their median.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import baselines, climatology, openmeteo, scoring
from .leagues import get_league
from .stations import data_dir, load_stations
from .truth_sources import daily_truth

log = logging.getLogger(__name__)

MAX_DAYS = 7
EVENT_MODELS = ("best_match", "gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless", "ukmo_seamless")


@dataclass(frozen=True)
class Event:
    event_id: str
    title: str
    league: str
    start: date
    end: date
    cutoff_utc: datetime
    stations: tuple[str, ...]
    status: str = "open"

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def matured(self, now: datetime | None = None) -> bool:
        """Truth for the last day is in once the league would resolve a round
        whose 72h window ended on `end`."""
        lg = get_league(self.league)
        return (now or datetime.now(timezone.utc)) >= lg.matured_at_utc(self.end - timedelta(days=2))


def events_dir() -> Path:
    return data_dir() / "events"


def load_event(event_id: str) -> Event:
    m = json.loads((events_dir() / event_id / "event.json").read_text())
    return Event(event_id=m["event_id"], title=m["title"], league=m["league"],
                 start=date.fromisoformat(m["start"]), end=date.fromisoformat(m["end"]),
                 cutoff_utc=datetime.fromisoformat(m["cutoff_utc"]), stations=tuple(m["stations"]),
                 status=m.get("status", "open"))


def open_event(title: str, league: str, start: date, end: date,
               bbox: tuple[float, float, float, float] | None = None, cutoff_hours_before: int = 24) -> Path:
    """bbox = (min_lat, min_lon, max_lat, max_lon). The cutoff is
    cutoff_hours_before the league's own cutoff for a round starting on `start`."""
    if not 1 <= (end - start).days + 1 <= MAX_DAYS:
        raise ValueError(f"event window must be 1-{MAX_DAYS} days")
    lg = get_league(league)
    sts = [s for s in load_stations(league)
           if bbox is None or (bbox[0] <= s.latitude <= bbox[2] and bbox[1] <= s.longitude <= bbox[3])]
    if not sts:
        raise ValueError("no stations in that region")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    event_id = f"{start.isoformat()}-{slug}"
    cutoff = lg.cutoff_utc(start) - timedelta(hours=cutoff_hours_before)
    manifest = {
        "event_id": event_id, "title": title, "league": league, "truth_source": lg.truth_source,
        "start": start.isoformat(), "end": end.isoformat(), "bbox": bbox,
        "cutoff_utc": cutoff.isoformat(), "stations": [s.station_id for s in sts],
        "featured": [s.station_id for s in sts if s.featured],
        "status": "open", "opened_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    d = events_dir() / event_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / "event.json"
    path.write_text(json.dumps(manifest, indent=1))
    sub = data_dir() / "submissions" / "events" / event_id
    sub.mkdir(parents=True, exist_ok=True)
    (sub / ".gitkeep").touch()
    return path


def storm_truth(ev: Event) -> pd.DataFrame:
    """station_id, truth_in, valid — the window total, valid only if every day is."""
    lg = get_league(ev.league)
    daily = daily_truth(lg, list(ev.stations), ev.start, ev.end)
    rows = []
    for sid, g in daily.groupby("station_id"):
        g = g.set_index("date")
        ok = all(d in g.index and bool(g.loc[d, "valid"]) for d in ev.days)
        rows.append({"station_id": sid, "truth_in": float(g["snow24"].sum()) if ok else float("nan"), "valid": ok})
    return pd.DataFrame(rows, columns=["station_id", "truth_in", "valid"])


def climatology_total(ev: Event) -> pd.DataFrame:
    """Sum of each day's climatological mean 24h snowfall (the no-skill storm)."""
    climo = climatology.load_climatology(ev.league)
    doys = [min(d.timetuple().tm_yday, 365) for d in ev.days]
    c = climo[climo["station_id"].isin(ev.stations) & climo["doy"].isin(doys)]
    tot = c.groupby("station_id")["h24_mean"].agg(["sum", "count"])
    tot = tot[tot["count"] == len(doys)]
    return pd.DataFrame({"station_id": tot.index, "snowfall_in": tot["sum"].round(3).values})


def model_totals(ev: Event, mode: str = "live") -> dict[str, pd.DataFrame]:
    """Each model's storm total (featured stations; all if none featured), + median."""
    sts = [s for s in load_stations(ev.league) if s.station_id in set(ev.stations)]
    sts = [s for s in sts if s.featured] or sts
    fetch = openmeteo.forecast_daily_snowfall if mode == "live" else openmeteo.hindcast_daily_snowfall
    out = {}
    for model in EVENT_MODELS:
        try:
            daily = fetch(sts, ev.start, ev.end, model=model)
        except Exception:
            log.warning("event model %s failed", model, exc_info=True)
            continue
        g = daily.groupby("station_id")["snowfall_in"]
        tot = g.sum(min_count=1)[g.count() == len(ev.days)].round(3)
        if len(tot):
            team = "baseline-openmeteo" if model == "best_match" else f"baseline-{model.split('_')[0]}"
            out[team] = pd.DataFrame({"station_id": tot.index, "snowfall_in": tot.values})
    if len(out) >= 3:
        allp = pd.concat(out.values())
        g = allp.groupby("station_id")["snowfall_in"]
        med = g.median()[g.count() >= 3].round(3)
        out[baselines.CONSENSUS_TEAM] = pd.DataFrame({"station_id": med.index, "snowfall_in": med.values})
    return out


def submit_event_baselines(ev: Event, mode: str = "live") -> list[Path]:
    sub = data_dir() / "submissions" / "events" / ev.event_id
    sub.mkdir(parents=True, exist_ok=True)
    teams = {"baseline-climatology": climatology_total(ev), **model_totals(ev, mode)}
    paths = []
    for team, pred in teams.items():
        p = sub / f"{team}.csv"
        pred.to_csv(p, index=False)
        paths.append(p)
    return paths


def _as_round(pred: pd.DataFrame) -> pd.DataFrame:
    """Reuse the round scorer: a storm total is a single 'horizon'."""
    return pred.assign(horizon_h=0)


def resolve_event(ev: Event, now: datetime | None = None) -> dict | None:
    if not ev.matured(now):
        return None
    truth = _as_round(storm_truth(ev))
    climo = _as_round(climatology_total(ev))
    from .rounds import GRACE_MINUTES, _first_commit_utc

    deadline = ev.cutoff_utc + timedelta(minutes=GRACE_MINUTES)
    teams = {}
    for path in sorted((data_dir() / "submissions" / "events" / ev.event_id).glob("*.csv")):
        pred = pd.read_csv(path)
        if not {"station_id", "snowfall_in"} <= set(pred.columns):
            teams[path.stem] = {"invalid": True, "errors": ["need station_id, snowfall_in"]}
            continue
        pred = pred[pred["station_id"].isin(ev.stations)]
        m = scoring.score_round(_as_round(pred), truth, climo_pred=climo)
        m.pop("mae_by_horizon", None)
        m.pop("brier6", None)
        if not path.stem.startswith("baseline-"):
            landed = _first_commit_utc(path)  # merge time on main, same rule as rounds
            m["late"] = landed is None or landed > deadline
        teams[path.stem] = m
    valid = truth[truth["valid"]]
    top = valid.sort_values("truth_in").tail(1)
    payload = {
        "event_id": ev.event_id, "title": ev.title, "league": ev.league,
        "start": ev.start.isoformat(), "end": ev.end.isoformat(),
        "resolved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stations_valid": int(len(valid)), "stations_voided": int((~truth["valid"]).sum()),
        "biggest_total": None if top.empty else {"station_id": top.iloc[0]["station_id"],
                                                  "inches": round(float(top.iloc[0]["truth_in"]), 1)},
        "teams": teams,
    }
    out = data_dir() / "results" / "events" / f"{ev.event_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    mpath = events_dir() / ev.event_id / "event.json"
    m = json.loads(mpath.read_text())
    m["status"] = "resolved"
    mpath.write_text(json.dumps(m, indent=1))
    return payload


def resolve_matured_events() -> list[str]:
    done = []
    if not events_dir().exists():
        return done
    for d in sorted(events_dir().iterdir()):
        if (d / "event.json").exists():
            ev = load_event(d.name)
            if ev.status == "open" and resolve_event(ev):
                done.append(ev.event_id)
    return done

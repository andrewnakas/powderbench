"""Storm Mode: window totals, all-days-valid rule, scoring."""

import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from powderbench import events
from powderbench import stations as stations_mod

DATA = Path(__file__).resolve().parents[1] / "data"


def test_storm_event_lifecycle(tmp_path, monkeypatch):
    for name in ("stations.yaml", "leagues.yaml"):
        shutil.copy(DATA / name, tmp_path / name)
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    stations_mod.clear_cache()
    try:
        path = events.open_event("Test Storm", "stations", date(2026, 2, 15), date(2026, 2, 17),
                                 bbox=(40.5, -111.7, 40.65, -111.5))  # Alta/Snowbird/Brighton
        ev = events.load_event(path.parent.name)
        assert ev.stations and len(ev.days) == 3
        sids = list(ev.stations)

        def fake_truth(league, ids, b, e):
            rows = []
            for i, sid in enumerate(ids):
                for k in range(3):
                    voided = i == 0 and k == 1  # first station loses a day -> no total
                    rows.append({"station_id": sid, "date": b + timedelta(days=k), "snow24": 10.0,
                                 "valid": not voided, "qc_flag": "ok"})
            return pd.DataFrame(rows)

        monkeypatch.setattr(events, "daily_truth", fake_truth)
        monkeypatch.setattr(events, "climatology_total",
                            lambda ev: pd.DataFrame({"station_id": sids, "snowfall_in": 3.0}))
        truth = events.storm_truth(ev).set_index("station_id")
        assert not truth.loc[sids[0], "valid"]
        assert all(truth.loc[s, "truth_in"] == 30.0 for s in sids[1:])

        sub = tmp_path / "submissions" / "events" / ev.event_id
        pd.DataFrame({"station_id": sids, "snowfall_in": 30.0}).to_csv(sub / "baseline-perfect.csv", index=False)
        assert events.resolve_event(ev, now=datetime(2026, 2, 10, tzinfo=timezone.utc)) is None  # not matured
        res = events.resolve_event(ev, now=datetime(2026, 3, 1, tzinfo=timezone.utc))
        assert res["teams"]["baseline-perfect"]["powder_score"] == 100.0
        assert res["stations_voided"] == 1
        assert events.load_event(ev.event_id).status == "resolved"
    finally:
        monkeypatch.delenv("POWDERBENCH_DATA_DIR")
        stations_mod.clear_cache()

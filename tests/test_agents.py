"""House agents (analog, gbm), the forecast brief and the MCP tools, on a
synthetic ERA5 history where GFS runs 2x high."""

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from powderbench import stations as stations_mod
from powderbench.leagues import get_league

SIDS = ["portillo:CL:ERA5", "valle-nevado:CL:ERA5"]
TARGET = date(2026, 9, 30)


@pytest.fixture
def history(tmp_path, monkeypatch):
    data = Path(__file__).resolve().parents[1] / "data"
    for n in ("stations.yaml", "leagues.yaml"):
        shutil.copy(data / n, tmp_path / n)
    monkeypatch.setenv("POWDERBENCH_DATA_DIR", str(tmp_path))
    stations_mod.clear_cache()
    rng = np.random.default_rng(0)

    def write_models(d, truth_by_sid):
        sub = tmp_path / "submissions" / "era5" / d.isoformat(); sub.mkdir(parents=True)
        for team, mult in (("gfs", 2.0), ("icon", 1.0), ("gem", 1.2)):
            rows = [{"station_id": s, "horizon_h": h, "snowfall_in": round(mult * t * h / 24, 2)}
                    for s, t in truth_by_sid.items() for h in (24, 48, 72)]
            pd.DataFrame(rows).to_csv(sub / f"baseline-{team}.csv", index=False)

    for i in range(90):
        d = TARGET - timedelta(days=90 - i)
        truth = {s: float(rng.gamma(0.6, 3.0)) for s in SIDS}
        write_models(d, truth)
        rd = tmp_path / "rounds" / "era5" / d.isoformat(); rd.mkdir(parents=True)
        pd.DataFrame([{"station_id": s, "horizon_h": h, "truth_in": t * h / 24, "valid": True}
                      for s, t in truth.items() for h in (24, 48, 72)]).to_csv(rd / "truth.csv", index=False)
    write_models(TARGET, {SIDS[0]: 8.0, SIDS[1]: 0.0})
    lg = get_league("era5")
    rd = tmp_path / "rounds" / "era5" / TARGET.isoformat(); rd.mkdir(parents=True)
    (rd / "round.json").write_text(json.dumps({
        "round_id": TARGET.isoformat(), "status": "open", "cutoff_utc": lg.cutoff_utc(TARGET).isoformat(),
        "target_days": [TARGET.isoformat()], "stations": SIDS}))
    yield lg
    stations_mod.clear_cache()


def _check(out):
    assert set(out["horizon_h"]) == {24, 48, 72}
    q = out[["p10", "p25", "p50", "p75", "p90"]].to_numpy()
    assert (np.diff(q, axis=1) >= 0).all() and (q >= 0).all()
    assert out.loc[out["horizon_h"] == 24, "prob_6in"].between(0, 1).all()
    return out.set_index(["station_id", "horizon_h"])["snowfall_in"]


def test_analog_agent(history):
    from powderbench.agents.analog import analog_agent

    s = _check(analog_agent(history, TARGET, history.cutoff_utc(TARGET)))
    assert s[(SIDS[0], 24)] > s[(SIDS[1], 24)]
    assert analog_agent(history, TARGET, datetime(2026, 6, 1, tzinfo=timezone.utc)).empty  # nothing resolved yet


def test_gbm_agent_learns_bias(history, monkeypatch):
    pytest.importorskip("lightgbm")
    from powderbench.agents import gbm
    from powderbench.agents.gbm import gbm_agent

    monkeypatch.setattr(gbm, "MIN_ROWS", 200)  # 60-round lookback x 2 stations x 3 horizons

    s = _check(gbm_agent(history, TARGET, history.cutoff_utc(TARGET)))
    assert s[(SIDS[0], 24)] > s[(SIDS[1], 24)]
    assert s[(SIDS[0], 24)] < 16.0 * 0.85  # discounts the 2x-high GFS
    assert gbm_agent(history, TARGET, datetime(2026, 6, 1, tzinfo=timezone.utc)).empty


def test_brief_and_mcp_tools(history):
    from powderbench.agents.brief import brief_markdown, build_brief

    b = build_brief(history, TARGET)
    assert [s["station_id"] for s in b["stations"]] == SIDS
    assert b["stations"][0]["models"]["gfs"][24] == 16.0
    assert {"gfs", "icon", "gem"} <= set(b["model_recent_mae_in"])
    md = brief_markdown(b)
    assert "station_id,horizon_h,snowfall_in" in md and SIDS[0] in md

    mcp_server = pytest.importorskip("powderbench.mcp_server")
    assert "## Stations" in mcp_server.get_brief("era5", TARGET.isoformat())
    csv = "station_id,horizon_h,snowfall_in\n" + "".join(f"{s},{h},1.0\n" for s in SIDS for h in (24, 48, 72))
    assert "PASS" in mcp_server.validate_forecast("era5", csv, TARGET.isoformat())
    assert "FAIL" in mcp_server.validate_forecast("era5", "station_id,horizon_h,snowfall_in\nnope,24,1\n", TARGET.isoformat())

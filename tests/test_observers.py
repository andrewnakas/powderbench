"""Observer networks: per-station day attribution and source QC."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from powderbench.networks import attribution, eccc, ghcnd


def _series(sid, values, start=date(2025, 11, 1)):
    return pd.DataFrame({"station_id": sid, "date": [start + timedelta(days=i) for i in range(len(values))],
                         "snow_in": values})


def test_fit_offsets_finds_morning_and_evening_observers():
    rng = np.random.default_rng(0)
    storms = np.where(rng.random(150) < 0.25, rng.gamma(2, 3, 150), 0.0)
    era5 = pd.DataFrame({"station_id": "x", "date": [date(2025, 11, 1) + timedelta(days=i) for i in range(150)],
                         "snowfall_in": storms})
    morning = _series("m", np.r_[0.0, storms[:-1]])  # filed the next morning
    evening = _series("e", storms)  # filed same day
    noise = _series("n", rng.random(150))
    fits = attribution.fit_offsets(pd.concat([morning, evening, noise]),
                                   pd.concat([era5.assign(station_id=s) for s in "men"]))
    assert fits["m"]["offset"] == 1
    assert fits["e"]["offset"] == 0
    assert fits["n"]["offset"] is None  # doesn't track storms -> dropped


def test_ghcnd_daily_applies_per_station_offsets(monkeypatch):
    raw = pd.DataFrame([
        {"station_id": "a", "date": date(2026, 1, 11), "snow_in": 5.0, "qflag": ""},  # morning: day 10
        {"station_id": "b", "date": date(2026, 1, 10), "snow_in": 3.0, "qflag": ""},  # evening: day 10
        {"station_id": "c", "date": date(2026, 1, 11), "snow_in": 9.0, "qflag": "X"},  # NCEI QC failed
    ])
    monkeypatch.setattr(ghcnd, "fetch_snow", lambda ids, b, e: raw)
    out = ghcnd.daily(["a", "b", "c"], date(2026, 1, 10), date(2026, 1, 10), {"b": 0}).set_index("station_id")
    assert out.loc["a", "snow24"] == 5.0 and out.loc["b", "snow24"] == 3.0
    assert out.loc["c", "qc_flag"] == ghcnd.QC_SOURCE_QC and not out.loc["c", "valid"]


def test_eccc_flags_void_accumulated_and_estimated(monkeypatch):
    raw = pd.DataFrame([
        {"station_id": "w", "date": date(2026, 1, 10), "snow_in": 4.0, "flag": ""},
        {"station_id": "acc", "date": date(2026, 1, 10), "snow_in": 12.0, "flag": "A"},
        {"station_id": "est", "date": date(2026, 1, 10), "snow_in": 2.0, "flag": "E"},
        {"station_id": "tr", "date": date(2026, 1, 10), "snow_in": 0.0, "flag": "T"},
    ])
    monkeypatch.setattr(eccc, "fetch_snow", lambda ids, b, e: raw)
    out = eccc.daily(["w", "acc", "est", "tr", "none"], date(2026, 1, 10), date(2026, 1, 10)).set_index("station_id")
    assert out.loc["w", "valid"] and out.loc["w", "snow24"] == 4.0
    assert not out.loc["acc", "valid"] and not out.loc["est", "valid"]  # multi-day / estimated
    assert out.loc["tr", "valid"] and out.loc["tr", "snow24"] == 0.0
    assert out.loc["none", "qc_flag"] == "missing"


def test_eccc_dms_coordinates():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("gen", Path(__file__).parents[1] / "scripts/networks/eccc.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert abs(gen.dms(500404000) - 50.0678) < 1e-3  # Whistler Roundhouse, 50°04'04"
    assert abs(gen.dms(-1225650000) + 122.9472) < 1e-3


def test_slf_live_overrides_bulk_and_qc(monkeypatch):
    from powderbench.networks import slf

    bulk = pd.DataFrame([
        {"station_code": "4ZE0", "date": date(2026, 1, 11), "HN_1D": 20.0},  # stale bulk value
        {"station_code": "4ZE0", "date": date(2026, 1, 12), "HN_1D": -1.0},  # sentinel
        {"station_code": "5WJ0", "date": date(2026, 1, 11), "HN_1D": 400.0},  # impossible
    ])
    live = pd.DataFrame([{"station_code": "4ZE0", "date": date(2026, 1, 11), "HN_1D": 25.4}])
    monkeypatch.setattr(slf, "_bulk", lambda net: bulk)
    monkeypatch.setattr(slf, "_live", lambda net, codes: live)
    monkeypatch.setattr(slf, "date", type("D", (), {"today": staticmethod(lambda: date(2026, 1, 13))}))
    out = slf.daily(["4ZE0:CH:SLF", "5WJ0:CH:SLF"], date(2026, 1, 10), date(2026, 1, 11)).set_index(["station_id", "date"])
    # study plots read at 07:00 -> day D <- report dated D+1; live API wins over bulk
    assert out.loc[("4ZE0:CH:SLF", date(2026, 1, 10)), "snow24"] == 10.0
    assert out.loc[("4ZE0:CH:SLF", date(2026, 1, 11)), "qc_flag"] == "implausible_jump"  # -1 sentinel
    assert out.loc[("5WJ0:CH:SLF", date(2026, 1, 10)), "qc_flag"] == "implausible_jump"


def test_documented_offset_waives_margin_but_must_still_win():
    rng = np.random.default_rng(1)
    storms = np.where(rng.random(150) < 0.3, rng.gamma(2, 3, 150), 0.0)
    days = [date(2025, 11, 1) + timedelta(days=i) for i in range(150)]
    era5 = pd.DataFrame({"station_id": "z", "date": days, "snowfall_in": storms})
    # a 07:00 reading splits each storm across two report days -> close call
    blend = 0.52 * np.r_[0.0, storms[:-1]] + 0.48 * storms  # r(+1)=0.73 vs r(0)=0.67
    reports = pd.DataFrame({"station_id": "z", "date": days, "snow_in": blend})
    assert attribution.fit_offsets(reports, era5)["z"]["offset"] is None  # guessing: too close
    assert attribution.fit_offsets(reports, era5, expected={"z": 1})["z"]["offset"] == 1
    assert attribution.fit_offsets(reports, era5, expected={"z": 0})["z"]["offset"] is None  # data disagrees


def test_cdec_sensor_qc_blanks_stuck_and_spiking_depth():
    from powderbench.networks import cdec

    g = pd.DataFrame({
        "SNWD": [50, 70, 134, 134, 134, 90, 95, 120, 96],
        "WTEQ": [12, 14, 16.3, 16.9, 17.8, 17.7, 17.7, 17.8, 17.9],
    })
    d = cdec._sensor_qc(g)
    assert d[:2].tolist() == [50, 70]
    assert d[2:5].isna().all()  # stuck at a ceiling while SWE climbed 1.5"
    assert d[7] != d[7]  # 120 between 95 and 96: a spike
    assert d[8] == 96


def test_snolite_big_jump_needs_neighbour_swe(monkeypatch):
    from powderbench.networks import snolite

    monkeypatch.setattr(snolite, "neighbors", lambda ids: {"lite:OR:SNTLT": ["n:OR:SNTL"], "lone:OR:SNTLT": []})

    def fake(ids, b, e, elements):
        days = [b + timedelta(days=i) for i in range(3)]
        if elements == ("SNWD",):
            return pd.DataFrame([{"station_id": s, "date": d, "SNWD": v}
                                 for s in ids for d, v in zip(days, [20, 30, 31])])
        return pd.DataFrame([{"station_id": "n:OR:SNTL", "date": d, "WTEQ": v} for d, v in zip(days, [5, 6, 6])])

    monkeypatch.setattr(snolite.snotel, "fetch_daily", fake)
    out = snolite.daily(["lite:OR:SNTLT", "lone:OR:SNTLT"], date(2026, 1, 2), date(2026, 1, 3)).set_index(["station_id", "date"])
    assert out.loc[("lite:OR:SNTLT", date(2026, 1, 2)), "snow24"] == 10  # neighbour gained 1" SWE
    assert out.loc[("lone:OR:SNTLT", date(2026, 1, 2)), "qc_flag"] == snolite.QC_NO_NEIGHBOR_SUPPORT
    assert out.loc[("lone:OR:SNTLT", date(2026, 1, 3)), "snow24"] == 1  # small days need no support

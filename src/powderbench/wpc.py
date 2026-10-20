"""NOAA WPC Probabilistic Winter Precipitation Forecast (PWPF) — the official
NWS snowfall percentiles (5th..95th) and exceedance probabilities on the
2.5 km CONUS grid. Public domain.

It is the natural probabilistic villain: WPC publishes exactly our quantiles
(p10/p25/p50/p75/p90) and P(>= 6"), for 24/48/72h windows. Only ~1.5 days of
files stay online, so the baseline submission written each round is also our
archive of it.

Alignment for round D (station-local days; mountain midnight ~06-08 UTC):
the 00Z cycle of day D is posted by ~18 UTC on D-1, before the 00 UTC cutoff.
  h24 <- 24h window ending 06Z D+1 (f030)
  h48 <- 48h window ending 06Z D+2 (f054)
  h72 <- 72h window ending 00Z D+3 (f072; WPC's longest — 6h early)
Snow in the GRIB is metres; probabilities are 0-1.

Needs the optional `eccodes` dependency (pip install -e .[grib]).
"""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from . import HORIZONS

log = logging.getLogger(__name__)

BASE = "https://ftp.wpc.ncep.noaa.gov/pwpf/conus_2.5km/"
M_TO_IN = 39.3701
MAX_GRID_KM = 5.0  # stations off the CONUS grid (Alaska) find no neighbour this close
PCTS = {"p10": "10", "p25": "25", "p50": "50", "p75": "75", "p90": "90"}
# horizon -> (window hours, forecast hour) from the 00Z cycle of round day D
WINDOWS = {24: (24, 30), 48: (48, 54), 72: (72, 72)}
PROB6 = {24: "psnow24_ge6", 48: None, 72: None}


def _url(window_h: int, name: str) -> str:
    return f"{BASE}2.5kmpwpf_{window_h}hr/{name}"


def _download(url: str, dest: Path) -> bool:
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 404:
                return False
            r.raise_for_status()
            dest.write_bytes(r.content)
            return True
        except requests.RequestException as exc:
            log.warning("WPC fetch failed (%s), retry %d", exc, attempt + 1)
            time.sleep(5 * (attempt + 1))
    return False


class _Grid:
    """Nearest-gridpoint index per station, computed once per run: bin the
    grid's lat/lon at BIN_DEG, then search each station's 3x3 bin block."""

    BIN_DEG = 0.05

    def __init__(self, gid, stations):
        import eccodes
        import numpy as np

        lats = eccodes.codes_get_array(gid, "latitudes")
        lons = (eccodes.codes_get_array(gid, "longitudes") + 180) % 360 - 180
        bi = np.floor(lats / self.BIN_DEG).astype(np.int64)
        bj = np.floor(lons / self.BIN_DEG).astype(np.int64)
        key = bi * 100_000 + bj
        order = np.argsort(key, kind="stable")
        skey = key[order]
        self.index: dict[str, int] = {}
        for s in stations:
            i0, j0 = int(np.floor(s.latitude / self.BIN_DEG)), int(np.floor(s.longitude / self.BIN_DEG))
            cand = []
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    k = (i0 + di) * 100_000 + (j0 + dj)
                    lo, hi = np.searchsorted(skey, [k, k + 1])
                    cand.append(order[lo:hi])
            idx = np.concatenate(cand)
            if not len(idx):
                continue
            # equirectangular distance is plenty at 2.5 km spacing
            dy = (lats[idx] - s.latitude) * 111.2
            dx = (lons[idx] - s.longitude) * 111.2 * np.cos(np.radians(s.latitude))
            d2 = dx * dx + dy * dy
            best = int(np.argmin(d2))
            if d2[best] <= MAX_GRID_KM**2:
                self.index[s.station_id] = int(idx[best])


def _read(path: Path, stations, grid: _Grid | None):
    import eccodes

    with path.open("rb") as f:
        gid = eccodes.codes_grib_new_from_file(f)
        try:
            grid = grid or _Grid(gid, stations)
            values = eccodes.codes_get_values(gid)
        finally:
            eccodes.codes_release(gid)
    return {sid: float(values[i]) for sid, i in grid.index.items()}, grid


def wpc_prediction(stations, round_day: date) -> pd.DataFrame:
    """Submission rows (point = median) for round D from the 00Z D cycle."""
    try:
        import eccodes  # noqa: F401
    except ImportError:
        log.warning("eccodes not installed — skipping the WPC baseline (pip install -e .[grib])")
        return pd.DataFrame()

    cycle = round_day.strftime("%Y%m%d") + "00"
    vals: dict[tuple[int, str], dict[str, float]] = {}
    grid = None
    with tempfile.TemporaryDirectory() as tmp:
        for h in HORIZONS:
            win, fh = WINDOWS[h]
            files = {col: f"2.5kmprcntil_{win}hsnow_{pct}pt_{cycle}f{fh:03d}.grb" for col, pct in PCTS.items()}
            if PROB6[h]:
                files["prob_6in"] = f"{PROB6[h]}_{cycle}f{fh:03d}.grb"
            for col, name in files.items():
                dest = Path(tmp) / name
                if not _download(_url(win, name), dest):
                    log.warning("WPC %s missing", name)
                    continue
                pts, grid = _read(dest, stations, grid)
                vals[(h, col)] = pts

    rows = []
    for s in stations:
        for h in HORIZONS:
            qs = {col: vals.get((h, col), {}).get(s.station_id) for col in PCTS}
            if any(v is None for v in qs.values()):
                continue
            row = {"station_id": s.station_id, "horizon_h": h}
            # WPC's fitted distributions dip below zero (= no snow): clip, and
            # sort to keep the quantiles monotone after rounding
            q_in = sorted(round(max(v, 0.0) * M_TO_IN, 2) for v in qs.values())
            row.update(dict(zip(PCTS, q_in)))
            row["snowfall_in"] = row["p50"]
            p6 = vals.get((h, "prob_6in"), {}).get(s.station_id)
            if p6 is not None:
                row["prob_6in"] = round(min(max(p6, 0.0), 1.0), 3)
            rows.append(row)
    cols = ["station_id", "horizon_h", "snowfall_in", *PCTS, "prob_6in"]
    return pd.DataFrame(rows).reindex(columns=cols) if rows else pd.DataFrame()

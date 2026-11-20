"""Where each producer keeps its files, and how to turn them into snowfall
water equivalent accumulated from the run start (mm) at given forecast steps.

Publish delays were measured from the buckets' Last-Modified times on
2026-11-15 (hours after the cycle until the steps a round uses are online):
IFS 00z f144 7.6 h, 06z f90 6.5 h; AIFS f96 5.3-5.4 h; GFS f096 4.0-4.2 h.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import requests

from . import grib

log = logging.getLogger(__name__)

# Grid-cell choice: terrain-matched within this many cells (0 = nearest cell)
CELL_RADIUS = 1
G0 = 9.80665  # ECMWF orography is geopotential (m2 s-2)
ECMWF_BUCKET = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
# ECMWF's own server keeps the last ~4 days: the fallback when the AWS bucket
# throttles (2026-11-20: every GET from one IP answered 503 SlowDown for hours)
ECMWF_DIRECT = "https://data.ecmwf.int/forecasts"
ECMWF_BASES = (ECMWF_BUCKET, ECMWF_DIRECT)
GFS_BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"


def _fetch_all(requests_: list[tuple], workers: int) -> list[bytes | None]:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda a: grib.fetch(*a), requests_))


class _Points:
    """Grid-cell index per station set, reused across messages on one grid.
    terrain() -> (grid metadata, model terrain height m, land fraction) on the
    model grid, or None; without it (or on another grid) the nearest cell is used."""

    def __init__(self, lats: np.ndarray, lons: np.ndarray, elev_m: np.ndarray, terrain=None):
        self.lats, self.lons, self.elev_m, self.terrain = lats, lons, elev_m, terrain
        self._idx: dict[tuple, np.ndarray] = {}

    def _index(self, meta: dict) -> np.ndarray:
        key = tuple(meta[k] for k in grib.GRID_KEYS)
        if key not in self._idx:
            ter = self.terrain() if (self.terrain and CELL_RADIUS > 0) else None
            if ter is not None and tuple(ter[0][k] for k in grib.GRID_KEYS) == key:
                self._idx[key] = grib.terrain_index(meta, self.lats, self.lons, self.elev_m,
                                                    ter[1], ter[2], CELL_RADIUS)
            else:
                self._idx[key] = grib.point_index(meta, self.lats, self.lons)
        return self._idx[key]

    def take(self, message: bytes, water_mm: bool = False) -> np.ndarray:
        """Values at the stations; water_mm converts a water-equivalent field
        to mm from whatever unit the message declares."""
        values, meta = grib.decode(message)
        key = tuple(meta[k] for k in grib.GRID_KEYS)
        self._index(meta)
        out = grib.at_points(values, self._idx[key])
        if water_mm:
            out = out * grib.to_mm(meta["units"])
        return out


class Source:
    name: str
    label: str
    licence: str
    cycles: tuple[int, ...] = (0, 6, 12, 18)
    online_h: float  # hours after the cycle until every step a round needs is online

    def steps(self, run: datetime) -> list[int] | None:
        """Forecast steps (h) this run publishes; None when it is not on the server."""
        raise NotImplementedError

    _terrain: dict | None = None

    def terrain(self, run: datetime):
        """(grid metadata, terrain height m, land fraction) from the run's
        analysis step; the grid is fixed, so the first one read is reused."""
        if self._terrain is None:
            self._terrain = {}
        if "t" not in self._terrain:
            try:
                self._terrain["t"] = self._read_terrain(run)
            except Exception:  # noqa: BLE001 - fall back to the nearest cell
                log.warning("%s: no terrain field; using the nearest grid cell", self.name, exc_info=True)
                self._terrain["t"] = None
        return self._terrain["t"]

    def _read_terrain(self, run: datetime):
        raise NotImplementedError

    def accum(self, run: datetime, steps: list[int], lats, lons, elev_m) -> dict[int, np.ndarray] | None:
        """Snowfall water equivalent (mm) since the run start, per step and
        station; None when a message is missing (run incomplete)."""
        raise NotImplementedError


Layout = tuple[str, str, str, str]  # (folder, grid, stream, file suffix)


class EcmwfOpenData(Source):
    """ECMWF open data (CC BY 4.0): the `sf` field (snowfall water equivalent,
    accumulated from the run start). IFS writes it in metres, AIFS in kg m-2
    (= mm): the unit is read from each message.

    IFS comes at 0.1 deg (~9 km, the model's native resolution) from runs of
    late October 2026 on, in separate `-sfc` files; earlier runs only at
    0.25 deg. Each run takes the finest layout it has, so live forecasts use
    0.1 deg while last winter's hindcasts use 0.25 deg."""

    licence = "CC BY 4.0 (ECMWF open data)"
    workers = 3  # the bucket answers 503 Slow Down at 8 parallel requests

    def __init__(self, name: str, label: str, layouts: list[Layout], online_h: float):
        self.name, self.label, self.online_h = name, label, online_h
        self.layouts = layouts  # tried in order: finest first, and the layout changed over time
        self._layout: dict[datetime, tuple[str, Layout] | None] = {}

    def _url(self, run: datetime, step: int, base: str, folder: str, grid: str, stream: str, suffix: str) -> str:
        return (f"{base}/{run:%Y%m%d}/{run:%H}z/{folder}/{grid}/{stream}/"
                f"{run:%Y%m%d%H}0000-{step}h-{stream}-fc{suffix}")

    def _probe(self, run: datetime) -> tuple[str, Layout] | None:
        for lay in self.layouts:
            for base in ECMWF_BASES:
                try:
                    if grib.fetch(self._url(run, 0, base, *lay) + ".index") is not None:
                        return base, lay
                except (requests.RequestException, OSError) as exc:  # throttled: try the next server
                    log.warning("%s: %s unreachable (%s)", self.name, base, exc)
        return None

    def layout(self, run: datetime) -> tuple[str, Layout] | None:
        """(server, layout) of the run, or None when it is not published."""
        if run not in self._layout:
            self._layout[run] = self._probe(run)
        return self._layout[run]

    def grid(self, run: datetime) -> str | None:
        found = self.layout(run)
        return found[1][1] if found else None

    def steps(self, run: datetime) -> list[int] | None:
        found = self.layout(run)
        if found is None:
            return None
        folder, _, stream, _ = found[1]
        if folder.startswith("aifs"):
            return list(range(0, 361, 6))
        if stream == "scda":  # 06/18z until spring 2026
            return list(range(0, 91, 3))
        if run.hour in (0, 12):
            return list(range(0, 145, 3)) + list(range(150, 361, 6))
        return list(range(0, 145, 3))  # 06/18z on the oper stream

    def _with_fallback(self, base: str, read):
        """read(server) on the probed server, then on the others when it is
        throttled or fails mid-run (the same files, so the same byte offsets)."""
        order = [base, *(b for b in ECMWF_BASES if b != base)]
        for i, b in enumerate(order):
            try:
                return read(b)
            except (requests.RequestException, OSError) as exc:
                if i == len(order) - 1:
                    raise
                log.warning("%s: %s failed (%s), trying %s", self.name, b, exc, order[i + 1])
        return None

    def _read_terrain(self, run):
        base, lay = self.layout(run)

        def read(b):
            url = self._url(run, 0, b, *lay)
            fields = {e["param"]: e for e in grib.ecmwf_index(grib.fetch(url + ".index").decode())
                      if e.get("levtype") == "sfc"}
            z, meta = grib.decode(grib.fetch(url + ".grib2", int(fields["z"]["_offset"]),
                                             int(fields["z"]["_length"])))
            lsm, _ = grib.decode(grib.fetch(url + ".grib2", int(fields["lsm"]["_offset"]),
                                            int(fields["lsm"]["_length"])))
            return meta, z / G0, lsm

        return self._with_fallback(base, read)

    def accum(self, run, steps, lats, lons, elev_m):
        found = self.layout(run)
        if found is None:
            return None
        base, lay = found
        pts = _Points(lats, lons, elev_m, lambda: self.terrain(run))
        want = [s for s in steps if s > 0]
        msgs = self._with_fallback(base, lambda b: self._messages(run, want, b, lay))
        if msgs is None:
            return None
        out = {0: np.zeros(len(lats))} if 0 in steps else {}
        for s, m in zip(want, msgs):
            out[s] = pts.take(m, water_mm=True)
        return out

    def _messages(self, run, want, base, lay) -> list[bytes] | None:
        urls = [self._url(run, s, base, *lay) for s in want]
        indexes = _fetch_all([(u + ".index",) for u in urls], self.workers)
        if any(t is None for t in indexes):
            return None
        reqs = []
        for u, text in zip(urls, indexes):
            entry = next((e for e in grib.ecmwf_index(text.decode())
                          if e.get("param") == "sf" and e.get("levtype") == "sfc"), None)
            if entry is None:
                log.warning("%s: no sf field in %s", self.name, u.rsplit("/", 1)[-1])
                return None
            reqs.append((u + ".grib2", int(entry["_offset"]), int(entry["_length"])))
        msgs = _fetch_all(reqs, self.workers)
        return None if any(m is None for m in msgs) else msgs


class Gfs(Source):
    """NOAA GFS 0.25 deg on AWS (public domain). GFS has no snowfall field, so
    snow = precipitation x the share of each 3 h interval that the model's
    categorical-snow flag was on (CSNOW, averaged over GFS's 6 h buckets;
    the 3 h share is recovered by differencing the bucket averages)."""

    name = "gfs"
    label = "NOAA GFS 0.25"
    licence = "public domain (NOAA)"
    online_h = 4.5
    workers = 8
    STEP_H = 3

    def _url(self, run: datetime, step: int) -> str:
        return f"{GFS_BUCKET}/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{step:03d}"

    def steps(self, run):
        if grib.fetch(self._url(run, 0) + ".idx") is None:
            return None
        return list(range(0, 385, self.STEP_H))

    @staticmethod
    def _pick(rows: list[dict], var: str, stat: str, step: int, total: bool) -> dict | None:
        cand = [r for r in rows if r["var"] == var and r["level"] == "surface"
                and r.get("stat") == stat and r.get("end_h") == step and (r["start_h"] == 0 or not total)]
        if not cand:
            return None
        return min(cand, key=lambda r: r["start_h"]) if total else max(cand, key=lambda r: r["start_h"])

    def _read_terrain(self, run):
        url = self._url(run, 0)
        rows = grib.ncep_index(grib.fetch(url + ".idx").decode())
        pick = {r["var"]: r for r in rows if r["level"] == "surface" and r["var"] in ("HGT", "LAND")}
        hgt, meta = grib.decode(grib.fetch(url, pick["HGT"]["offset"], pick["HGT"]["length"]))
        land, _ = grib.decode(grib.fetch(url, pick["LAND"]["offset"], pick["LAND"]["length"]))
        return meta, hgt, land

    def accum(self, run, steps, lats, lons, elev_m):
        top = max(steps)
        chain = list(range(self.STEP_H, top + 1, self.STEP_H))
        urls = [self._url(run, s) for s in chain]
        idx_texts = _fetch_all([(u + ".idx",) for u in urls], self.workers)
        if any(t is None for t in idx_texts):
            return None
        reqs, buckets = [], []
        for s, u, text in zip(chain, urls, idx_texts):
            rows = grib.ncep_index(text.decode())
            apcp = self._pick(rows, "APCP", "acc", s, total=True)
            csnow = self._pick(rows, "CSNOW", "ave", s, total=False)
            if apcp is None or csnow is None:
                log.warning("gfs: APCP/CSNOW missing at f%03d of %s", s, run)
                return None
            reqs += [(u, apcp["offset"], apcp["length"]), (u, csnow["offset"], csnow["length"])]
            buckets.append(csnow["start_h"])
        msgs = _fetch_all(reqs, self.workers)
        if any(m is None for m in msgs):
            return None
        pts = _Points(lats, lons, elev_m, lambda: self.terrain(run))
        apcp = [pts.take(msgs[2 * k], water_mm=True) for k in range(len(chain))]
        csnow = [pts.take(msgs[2 * k + 1]) for k in range(len(chain))]
        cum = snow_from_buckets(chain, buckets, apcp, csnow)
        out = {t: c for t, c in zip(chain, cum) if t in steps}
        if 0 in steps:
            out[0] = np.zeros(len(lats))
        return out


def snow_from_buckets(chain: list[int], buckets: list[int], apcp: list[np.ndarray],
                      csnow: list[np.ndarray]) -> list[np.ndarray]:
    """Cumulative snow water (mm) at each step of `chain` (3 h apart, from 0).
    apcp[k]: precipitation accumulated from the run start to chain[k];
    csnow[k]: the categorical-snow average over (buckets[k], chain[k]]."""
    snow = np.zeros_like(apcp[0])
    p_prev, c_prev, b_prev, t_prev = np.zeros_like(snow), np.zeros_like(snow), 0, 0
    out = []
    for t, b, p, c in zip(chain, buckets, apcp, csnow):
        if b == t_prev:
            frac = c
        elif b == b_prev:  # same bucket: snow time in (t_prev, t] from the two averages
            frac = (c * (t - b) - c_prev * (t_prev - b)) / (t - t_prev)
        else:
            frac = c
        snow = snow + np.clip(p - p_prev, 0.0, None) * np.clip(frac, 0.0, 1.0)
        out.append(snow.copy())
        p_prev, c_prev, b_prev, t_prev = p, c, b, t
    return out


SOURCES: dict[str, Source] = {
    "ecmwf_ifs": EcmwfOpenData("ecmwf_ifs", "ECMWF IFS", [("ifs", "0p1", "oper", "-sfc"), ("ifs", "0p25", "oper", ""),
                                                        ("ifs", "0p25", "scda", "")], online_h=7.75),
    "ecmwf_aifs": EcmwfOpenData("ecmwf_aifs", "ECMWF AIFS single", [("aifs-single", "0p25", "oper", ""),
                                                                    ("aifs", "0p25", "oper", "")], online_h=6.0),
    "gfs": Gfs(),
}

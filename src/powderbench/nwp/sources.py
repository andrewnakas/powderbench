"""Where each producer keeps its files, and how to turn them into snowfall
water equivalent accumulated from the run start (mm) at given forecast steps.

Publish delays were measured from the buckets' Last-Modified times on
2026-11-15 (hours after the cycle until the steps a round uses are online):
IFS 00z f144 7.6 h, 06z f90 6.5 h; AIFS f96 5.3-5.4 h; GFS f096 4.0-4.2 h.
"""

from __future__ import annotations

import logging
import os
import time
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
# The same files on ECMWF's Google Cloud mirror (full archive) and on ECMWF's
# own server (the last ~4 days): the fallbacks when the AWS bucket throttles
# (2026-11-20: every GET from one IP answered 503 SlowDown for hours)
ECMWF_GCS = "https://storage.googleapis.com/ecmwf-open-data"
ECMWF_DIRECT = "https://data.ecmwf.int/forecasts"
ECMWF_BASES = (ECMWF_BUCKET, ECMWF_GCS, ECMWF_DIRECT)
# a server that failed is skipped for this long, so a throttled one does not
# cost its retries on every file of a long hindcast
SERVER_REST_S = 900
_server_down: dict[str, float] = {}


def _resting(server: str) -> bool:
    return time.monotonic() - _server_down.get(server, -1e12) < SERVER_REST_S


def _servers(first: str | None = None) -> list[str]:
    """ECMWF_BASES in order (first, if given, ahead), resting servers last."""
    order = [first, *(b for b in ECMWF_BASES if b != first)] if first else list(ECMWF_BASES)
    return sorted(order, key=_resting)
GFS_BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
HRRR_BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
NBM_BUCKET = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
# Open-Meteo's elevation rule (VariableHourly.swift, GenericReader.swift in
# open-meteo/open-meteo): where the chosen cell's terrain is more than
# RULE_MIN_DZ_M above or below the station, the 2 m temperature is moved to the
# station's height at LAPSE_K_PER_M, and snowfall is kept only while that
# temperature is below 0 C. PB_NWP_TRULE=0 turns it off.
LAPSE_K_PER_M = 0.0065
CM_PER_MM_WE_OM = 0.7  # Open-Meteo's snowfall (cm) per mm of water
RULE_MIN_DZ_M = 100.0


def below_zero_share(t0: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """Share of an interval below 0 C, the temperature linear between its ends."""
    t0, t1 = np.asarray(t0, float), np.asarray(t1, float)
    mixed = (t0 < 0) != (t1 < 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cross = np.where(t0 < 0, -t0 / (t1 - t0), -t1 / (t0 - t1))
    return np.where(mixed, cross, np.where(t0 < 0, 1.0, 0.0))


def temperature_rule(acc: list[np.ndarray], temp_c: list[np.ndarray], dz_m: np.ndarray) -> list[np.ndarray]:
    """Accumulated snow water (mm) at consecutive steps after the elevation
    rule. acc[i] is the run's accumulation at step i+1 (the run start is 0);
    temp_c[i] the cell's 2 m temperature at step i (0 = the run start); dz_m
    the cell's terrain height minus the station's."""
    dz = np.asarray(dz_m, float)
    apply, shift = np.abs(dz) > RULE_MIN_DZ_M, dz * LAPSE_K_PER_M
    cum, prev, out = np.zeros(len(dz)), np.zeros(len(dz)), []
    for i, a in enumerate(acc):
        inc = np.clip(a - prev, 0.0, None)
        keep = below_zero_share(temp_c[i] + shift, temp_c[i + 1] + shift)
        cum = cum + np.where(apply, inc * keep, inc)
        prev = a
        out.append(cum.copy())
    return out


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

    IFS also comes at 0.1 deg (~9 km, the model's native resolution) from
    runs of late October 2026 on, in separate `-sfc` files. Each run takes the
    first layout in the list that it has; IFS lists 0.25 deg first (see
    SOURCES), with 0.1 deg as the fallback."""

    licence = "CC BY 4.0 (ECMWF open data)"
    workers = 3  # the bucket answers 503 Slow Down at 8 parallel requests

    def __init__(self, name: str, label: str, layouts: list[Layout], online_h: float, trule: bool = False):
        self.name, self.label, self.online_h = name, label, online_h
        self.trule = trule  # apply Open-Meteo's elevation rule (needs every step's sf and 2t)
        self.layouts = layouts  # tried in order: finest first, and the layout changed over time
        self._layout: dict[datetime, tuple[str, Layout] | None] = {}

    def _url(self, run: datetime, step: int, base: str, folder: str, grid: str, stream: str, suffix: str) -> str:
        return (f"{base}/{run:%Y%m%d}/{run:%H}z/{folder}/{grid}/{stream}/"
                f"{run:%Y%m%d%H}0000-{step}h-{stream}-fc{suffix}")

    def _probe(self, run: datetime) -> tuple[str, Layout] | None:
        for lay in self.layouts:
            answered = False  # a server said "not there": a resting one is not asked too
            for base in _servers():
                if answered and _resting(base):
                    continue
                try:
                    if grib.fetch(self._url(run, 0, base, *lay) + ".index") is not None:
                        return base, lay
                    answered = True
                except (requests.RequestException, OSError) as exc:  # throttled: try the next server
                    _server_down[base] = time.monotonic()
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
        order = _servers(base)
        for i, b in enumerate(order):
            try:
                return read(b)
            except (requests.RequestException, OSError) as exc:
                _server_down[b] = time.monotonic()
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
        if self.trule and os.environ.get("PB_NWP_TRULE", "1") != "0":
            out = self._accum_trule(run, steps, lats, lons, elev_m, *found)
            if out is not None:
                return out
            log.warning("%s: elevation rule unavailable for %s; raw snowfall", self.name, run)
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

    def _accum_trule(self, run, steps, lats, lons, elev_m, base, lay):
        """accum() with the elevation rule: every step from the run start to
        the last one asked for, since the rule works interval by interval."""
        ter = self.terrain(run)
        if ter is None:
            return None
        full = [s for s in (self.steps(run) or []) if 0 < s <= max(steps)]
        if not full:
            return {s: np.zeros(len(lats)) for s in steps}
        sf = self._with_fallback(base, lambda b: self._messages(run, full, b, lay))
        t2 = self._with_fallback(base, lambda b: self._messages(run, [0, *full], b, lay, param="2t"))
        if sf is None or t2 is None:
            return None
        pts = _Points(lats, lons, elev_m, lambda: ter)
        acc = [pts.take(m, water_mm=True) for m in sf]
        temps = [pts.take(m) - 273.15 for m in t2]
        key = tuple(grib.decode(sf[0])[1][k] for k in grib.GRID_KEYS)
        if tuple(ter[0][k] for k in grib.GRID_KEYS) != key:
            return None
        dz = grib.at_points(ter[1], pts._idx[key]) - np.asarray(elev_m, float)
        out = dict(zip(full, temperature_rule(acc, temps, dz)))
        out[0] = np.zeros(len(lats))
        return {s: out[s] for s in steps}

    def _messages(self, run, want, base, lay, param: str = "sf") -> list[bytes] | None:
        urls = [self._url(run, s, base, *lay) for s in want]
        indexes = _fetch_all([(u + ".index",) for u in urls], self.workers)
        if any(t is None for t in indexes):
            return None
        reqs = []
        for u, text in zip(urls, indexes):
            entry = next((e for e in grib.ecmwf_index(text.decode())
                          if e.get("param") == param and e.get("levtype") == "sfc"), None)
            if entry is None:
                log.warning("%s: no %s field in %s", self.name, param, u.rsplit("/", 1)[-1])
                return None
            reqs.append((u + ".grib2", int(entry["_offset"]), int(entry["_length"])))
        msgs = _fetch_all(reqs, self.workers)
        return None if any(m is None for m in msgs) else msgs


def _ranges(entries: list[dict]) -> list[tuple[int, int, list[tuple[int, int]]]]:
    """Index entries -> byte ranges with adjacent messages merged: (offset,
    length, [(start, length) of each message inside the range])."""
    out: list[tuple[int, int, list[tuple[int, int]]]] = []
    for e in sorted(entries, key=lambda e: int(e["_offset"])):
        off, n = int(e["_offset"]), int(e["_length"])
        if out and out[-1][0] + out[-1][1] == off:
            o, ln, parts = out[-1]
            out[-1] = (o, ln + n, parts + [(ln, n)])
        else:
            out.append((off, n, [(0, n)]))
    return out


class EcmwfEns(EcmwfOpenData):
    """ECMWF IFS ensemble, 0.25 deg (CC BY 4.0): the mean of the 50 perturbed
    members' sf. The elevation rule takes the 2 m temperature of the
    deterministic run on the same grid (the members' own 2t would be 2.5 times
    the download for an interval-by-interval rule)."""

    min_members = 40

    def __init__(self):
        super().__init__("ecmwf_ens", "ECMWF IFS ENS mean", [("ifs", "0p25", "enfo", "")], online_h=8.5, trule=True)

    def _url(self, run, step, base, folder, grid, stream, suffix):
        return (f"{base}/{run:%Y%m%d}/{run:%H}z/{folder}/{grid}/{stream}/"
                f"{run:%Y%m%d%H}0000-{step}h-{stream}-ef{suffix}")

    def steps(self, run):
        if self.layout(run) is None:
            return None
        return list(range(0, 145, 3)) + (list(range(150, 361, 6)) if run.hour in (0, 12) else [])

    def _members(self, run, want, base, lay) -> list[list[bytes]] | None:
        urls = [self._url(run, s, base, *lay) for s in want]
        indexes = _fetch_all([(u + ".index",) for u in urls], self.workers)
        if any(t is None for t in indexes):
            return None
        reqs, layout = [], []
        for u, text in zip(urls, indexes):
            es = [e for e in grib.ecmwf_index(text.decode()) if e.get("param") == "sf" and e.get("levtype") == "sfc"]
            if len(es) < self.min_members:
                log.warning("%s: %d members in %s", self.name, len(es), u.rsplit("/", 1)[-1])
                return None
            rs = _ranges(es)
            layout.append((len(reqs), rs))
            reqs += [(u + ".grib2", o, n) for o, n, _ in rs]
        blobs = _fetch_all(reqs, self.workers)
        if any(b is None for b in blobs):
            return None
        return [[blobs[first + k][a:a + n] for k, (_, _, parts) in enumerate(rs) for a, n in parts]
                for first, rs in layout]

    def accum(self, run, steps, lats, lons, elev_m):
        found = self.layout(run)
        if found is None:
            return None
        base, lay = found
        hres = SOURCES["ecmwf_ifs"]
        rule = self.trule and os.environ.get("PB_NWP_TRULE", "1") != "0"
        want = [s for s in (self.steps(run) or []) if 0 < s <= max(steps)] if rule else [s for s in steps if s > 0]
        if not want:
            return {s: np.zeros(len(lats)) for s in steps}
        groups = self._with_fallback(base, lambda b: self._members(run, want, b, lay))
        if groups is None:
            return None
        pts = _Points(lats, lons, elev_m, lambda: hres.terrain(run))
        mean = [np.nanmean(np.vstack([pts.take(m, water_mm=True) for m in g]), axis=0) for g in groups]
        if rule:
            hfound = hres.layout(run)
            ter = hres.terrain(run)
            t2 = None
            if hfound is not None and ter is not None and hfound[1][1] == lay[1]:
                t2 = hres._with_fallback(hfound[0], lambda b: hres._messages(run, [0, *want], b, hfound[1], param="2t"))
            key = tuple(grib.decode(groups[0][0])[1][k] for k in grib.GRID_KEYS)
            if t2 is not None and tuple(ter[0][k] for k in grib.GRID_KEYS) == key:
                temps = [pts.take(m) - 273.15 for m in t2]
                dz = grib.at_points(ter[1], pts._idx[key]) - np.asarray(elev_m, float)
                mean = temperature_rule(mean, temps, dz)
            else:
                log.warning("%s: no deterministic 2t/terrain for %s; mean without the elevation rule", self.name, run)
        out = dict(zip(want, mean))
        out[0] = np.zeros(len(lats))
        return {s: out[s] for s in steps}


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
    trule = True  # Open-Meteo's elevation rule on the derived snow (see temperature_rule)

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
        if self.trule and os.environ.get("PB_NWP_TRULE") != "0":
            ruled = self._trule(run, chain, urls, idx_texts, pts, msgs[0], cum, elev_m)
            if ruled is None:
                log.warning("gfs: no 2 m temperature or terrain for %s; snow without the elevation rule", run)
            else:
                cum = ruled
        out = {t: c for t, c in zip(chain, cum) if t in steps}
        if 0 in steps:
            out[0] = np.zeros(len(lats))
        return out


    def _tmp2m(self, rows: list[dict]) -> dict | None:
        return next((r for r in rows if r["var"] == "TMP" and r["level"] == "2 m above ground"), None)

    def _trule(self, run, chain, urls, idx_texts, pts, any_msg, cum, elev_m):
        """cum after the elevation rule: 2 m temperature at the run start and
        every step of the chain, terrain height at the chosen cells."""
        ter = self.terrain(run)
        if ter is None:
            return None
        f0 = self._url(run, 0)
        t0_idx = grib.fetch(f0 + ".idx")
        rows = [grib.ncep_index(t0_idx.decode())] if t0_idx else [None]
        rows += [grib.ncep_index(t.decode()) for t in idx_texts]
        picks = [self._tmp2m(r) if r else None for r in rows]
        if any(p is None for p in picks):
            return None
        msgs = _fetch_all([(u, p["offset"], p["length"]) for u, p in zip([f0, *urls], picks)], self.workers)
        if any(m is None for m in msgs):
            return None
        temps = [pts.take(m) - 273.15 for m in msgs]
        key = tuple(grib.decode(any_msg)[1][k] for k in grib.GRID_KEYS)
        if tuple(ter[0][k] for k in grib.GRID_KEYS) != key:
            return None
        dz = grib.at_points(ter[1], pts._idx[key]) - np.asarray(elev_m, float)
        return temperature_rule(cum, temps, dz)


class Hrrr(Source):
    """NOAA HRRR, 3 km over the contiguous US (Lambert grid), on AWS (public
    domain). Snow is WEASD accumulated from the run start: the model's own
    frozen precipitation, water equivalent. 00/06/12/18z reach 48 h, so the
    third day of a round is left empty. The elevation rule uses the hourly
    2 m temperature; at 3 km it moves less than on the global grids."""

    name = "hrrr"
    label = "NOAA HRRR 3 km"
    licence = "public domain (NOAA)"
    online_h = 2.5
    workers = 8
    trule = True

    def _url(self, run: datetime, step: int) -> str:
        return f"{HRRR_BUCKET}/hrrr.{run:%Y%m%d}/conus/hrrr.t{run:%H}z.wrfsfcf{step:02d}.grib2"

    def steps(self, run):
        if grib.fetch(self._url(run, 0) + ".idx") is None:
            return None
        return list(range(0, 49))

    def _read_terrain(self, run):
        url = self._url(run, 0)
        rows = grib.ncep_index(grib.fetch(url + ".idx").decode())
        pick = {r["var"]: r for r in rows if r["level"] == "surface" and r["var"] in ("HGT", "LAND")}
        hgt, meta = grib.decode(grib.fetch(url, pick["HGT"]["offset"], pick["HGT"]["length"]))
        land, _ = grib.decode(grib.fetch(url, pick["LAND"]["offset"], pick["LAND"]["length"]))
        return meta, hgt, land

    def accum(self, run, steps, lats, lons, elev_m):
        top = max(steps)
        if top == 0:
            return {0: np.zeros(len(lats))}
        chain = list(range(1, top + 1))  # hourly: the rule works interval by interval
        urls = [self._url(run, s) for s in [0, *chain]]
        idx_texts = _fetch_all([(u + ".idx",) for u in urls], self.workers)
        if any(t is None for t in idx_texts):
            return None
        reqs = []
        for k, (u, text) in enumerate(zip(urls, idx_texts)):
            rows = grib.ncep_index(text.decode())
            tmp = next((r for r in rows if r["var"] == "TMP" and r["level"] == "2 m above ground"), None)
            if tmp is None:
                log.warning("hrrr: TMP 2 m missing at f%02d of %s", k, run)
                return None
            reqs.append((u, tmp["offset"], tmp["length"]))
            if k:
                we = next((r for r in rows if r["var"] == "WEASD" and r["level"] == "surface"
                           and r.get("stat") == "acc" and r.get("start_h") == 0 and r.get("end_h") == k), None)
                if we is None:
                    log.warning("hrrr: WEASD 0-%d h missing in %s", k, run)
                    return None
                reqs.append((u, we["offset"], we["length"]))
        msgs = _fetch_all(reqs, self.workers)
        if any(m is None for m in msgs):
            return None
        pts = _Points(lats, lons, elev_m, lambda: self.terrain(run))
        temps = [pts.take(msgs[0]) - 273.15] + [pts.take(msgs[2 * k - 1]) - 273.15 for k in chain]
        acc = [pts.take(msgs[2 * k], water_mm=True) for k in chain]
        if self.trule and os.environ.get("PB_NWP_TRULE") != "0":
            ter = self.terrain(run)
            key = tuple(grib.decode(msgs[0])[1][k] for k in grib.GRID_KEYS)
            if ter is not None and tuple(ter[0][k] for k in grib.GRID_KEYS) == key:
                dz = grib.at_points(ter[1], pts._idx[key]) - np.asarray(elev_m, float)
                acc = temperature_rule(acc, temps, dz)
            else:
                log.warning("hrrr: no terrain for %s; snow without the elevation rule", run)
        out = dict(zip(chain, acc))
        out[0] = np.zeros(len(lats))
        return {s: out[s] for s in steps}


class Nbm(Source):
    """NOAA National Blend of Models, 2.5 km over the contiguous US (Lambert
    grid), on AWS (public domain, archive from 2020). Snow is the blend's own
    ASNOW: calibrated snowfall depth with its own snow-to-liquid ratio, already
    on 2.5 km terrain, so no elevation rule. Buckets are 1 h to 36 h and 6 h
    after. accum() hands back ASNOW divided by Open-Meteo's 0.7 cm per mm, so
    the daily output (water x 0.7) is ASNOW itself."""

    name = "nbm"
    label = "NOAA NBM 2.5 km"
    licence = "public domain (NOAA)"
    online_h = 2.0  # every core file was up 1 h 07 after the cycle (2026-11-18)
    workers = 8
    hourly_to = 36

    def _url(self, run: datetime, step: int) -> str:
        return f"{NBM_BUCKET}/blend.{run:%Y%m%d}/{run:%H}/core/blend.t{run:%H}z.core.f{step:03d}.co.grib2"

    def steps(self, run):
        if grib.fetch(self._url(run, 1) + ".idx") is None:
            return None
        return list(range(0, self.hourly_to + 1)) + list(range(42, 157, 6))  # core files end at f159

    def chain(self, step: int) -> list[tuple[int, int]]:
        """(bucket start, bucket end) pieces that sum to the snow from 0 to step:
        6 h buckets as far as they go, then 1 h buckets (only up to hourly_to)."""
        six = step if step > self.hourly_to else step // 6 * 6
        return [(e - 6, e) for e in range(6, six + 1, 6)] + [(e - 1, e) for e in range(six + 1, step + 1)]

    def accum(self, run, steps, lats, lons, elev_m):
        from . import CM_PER_MM_WE

        pieces = sorted({p for s in steps for p in self.chain(s)}, key=lambda p: (p[1], p[0]))
        files = sorted({e for _, e in pieces})
        texts = _fetch_all([(self._url(run, e) + ".idx",) for e in files], self.workers)
        if any(t is None for t in texts):
            return None
        rows = dict(zip(files, (grib.ncep_index(t.decode()) for t in texts)))
        reqs = []
        for a, e in pieces:
            r = next((r for r in rows[e] if r["var"] == "ASNOW" and r["level"] == "surface" and not r["extra"]
                      and r.get("stat") == "acc" and r.get("start_h") == a and r.get("end_h") == e), None)
            if r is None:
                log.warning("nbm: ASNOW %d-%d h missing in %s", a, e, run)
                return None
            reqs.append((self._url(run, e), r["offset"], r["length"]))
        msgs = _fetch_all(reqs, self.workers)
        if any(m is None for m in msgs):
            return None
        pts = _Points(lats, lons, elev_m)
        snow = {}
        for p, m in zip(pieces, msgs):
            units = grib.decode(m)[1]["units"]
            if units not in ("m", "unknown"):  # WMO 0-1-29 "total snowfall" is in m; eccodes has no name for it here
                raise ValueError(f"nbm: ASNOW in {units!r}, expected m")
            snow[p] = np.clip(pts.take(m), 0, None) * 100.0 / CM_PER_MM_WE
        return {s: sum((snow[p] for p in self.chain(s)), np.zeros(len(lats))) for s in steps}


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


class OpenMeteoArchive(Source):
    """A model as Open-Meteo stores it (nwp/omdata.py: s3://openmeteo, CC BY
    4.0, per-run files kept from 2026-08 on), read at every station with no
    API calls. `domains` go finest first, as in Open-Meteo's *_seamless
    models: a station takes the first domain whose grid covers it, and hours
    past that domain's last valid time continue in the next one (same run).

    Snow per domain, from what the run has: snowfall_water_equivalent; else
    snowfall (cm, / 0.7); else precipitation while the 2 m temperature moved
    to the station's height is below 0 C (UKMO, JMA, ICON-CH, MET Nordic).
    For UKMO that matches the API's baseline best (Alps, 7 rounds: MAE
    0.045 in against 0.093 for precipitation minus rain). Same cell choice and
    elevation rule as the producer readers above. Regular, rotated and
    Lambert grids (nwp/omdata.grid)."""

    licence = "CC BY 4.0 (Open-Meteo open data)"

    def __init__(self, name: str, label: str, domains: list[str], online_h: float,
                 cycles: tuple[int, ...] = (0, 6, 12, 18), trule: bool = True, frozen: bool = False):
        self.name, self.label, self.domains, self.online_h = name, label, domains, online_h
        self.cycles, self.trule = cycles, trule
        self.frozen = frozen  # snow = precipitation below 0 C, whatever the run stores

    def steps(self, run):
        from . import omdata

        hours = omdata.run_hours(self.domains[-1], run)
        return None if hours is None else [int(round(h)) for h in hours if h >= 0]

    def _snow(self, dom: str, run: datetime, cells: np.ndarray, dz: np.ndarray) -> np.ndarray | None:
        """(stations, valid times) snow water (mm) in each interval; NaN rows
        where the domain has no data at the station."""
        from . import omdata

        have = omdata.run_variables(dom, run)
        rule = self.trule and os.environ.get("PB_NWP_TRULE", "1") != "0"

        n = len(omdata.run_hours(dom, run))

        def read(name):
            """(stations, valid times); amounts are stored per interval, one
            fewer than the valid times (none closes at the run start)."""
            if name not in have:
                return None
            v = omdata.run_values(dom, run, name, cells)
            if v is not None and v.shape[1] == n - 1:
                v = np.hstack([np.zeros((len(v), 1)), v])
            return v

        frozen_only = False
        if self.frozen:
            if (inc := read("precipitation")) is None:
                return None
            frozen_only = True
        elif (inc := read("snowfall_water_equivalent")) is None:
            if (cm := read("snowfall")) is not None:
                inc = cm / CM_PER_MM_WE_OM
            elif (pr := read("precipitation")) is not None:
                inc, frozen_only = pr, True
            else:
                return None
        inc = np.clip(inc, 0.0, None)
        inc[:, 0] = 0.0  # the run start closes no interval
        if rule or frozen_only:
            t2 = read("temperature_2m")
            if t2 is None:
                return None
            t2[:, 0] = np.where(np.isnan(t2[:, 0]), t2[:, 1], t2[:, 0])  # some runs leave the start empty
            far = np.abs(dz) > RULE_MIN_DZ_M
            shift = (np.where(far, dz * LAPSE_K_PER_M, 0.0) if rule else np.zeros_like(dz))[:, None]
            share = np.ones_like(inc)
            share[:, 1:] = below_zero_share(t2[:, :-1] + shift, t2[:, 1:] + shift)
            keep = share if frozen_only else np.where(far[:, None] & rule, share, 1.0)
            inc = inc * keep
            inc[np.isnan(t2)] = np.nan
        bad = np.isnan(inc[:, 1:]).any(axis=1)
        inc[:, 0] = 0.0
        inc[bad] = np.nan
        return inc

    def accum(self, run, steps, lats, lons, elev_m):
        from . import omdata

        lats, lons, elev_m = (np.asarray(a, float) for a in (lats, lons, elev_m))
        curves: list[tuple[np.ndarray, np.ndarray] | None] = [None] * len(lats)
        for dom in self.domains:
            hours = omdata.run_hours(dom, run)
            if hours is None:
                continue
            g, height, land = omdata.grid(dom)
            idx = grib.terrain_index(g, lats, lons, elev_m, height, land, CELL_RADIUS)
            todo = [k for k in range(len(lats))
                    if idx[k] >= 0 and (curves[k] is None or curves[k][0][-1] < hours[-1])]
            if not todo:
                continue
            cells = idx[todo]
            snow = self._snow(dom, run, cells, height[cells] - elev_m[todo])
            if snow is None:
                continue
            for row, k in enumerate(todo):
                if np.isnan(snow[row, 1:]).any():
                    continue  # outside the domain's data (e.g. AROME's missing corners)
                acc = np.cumsum(snow[row])
                if curves[k] is None:
                    curves[k] = (hours, acc)
                else:
                    h0, a0 = curves[k]
                    tail = hours > h0[-1]
                    base = a0[-1] - np.interp(h0[-1], hours, acc)
                    curves[k] = (np.concatenate([h0, hours[tail]]), np.concatenate([a0, acc[tail] + base]))
        x = np.asarray(steps, float)
        table = np.full((len(steps), len(lats)), np.nan)
        for k, c in enumerate(curves):
            if c is not None:
                table[:, k] = np.interp(x, c[0], c[1], right=np.nan)
        return {s: table[i] for i, s in enumerate(steps)}


SOURCES: dict[str, Source] = {
    # 0.25 deg first: with the elevation rule it scored better than 0.1 deg in
    # three of four leagues over every station (DATA.md), and it is smaller
    "ecmwf_ifs": EcmwfOpenData("ecmwf_ifs", "ECMWF IFS", [("ifs", "0p25", "oper", ""), ("ifs", "0p25", "scda", ""),
                                                        ("ifs", "0p1", "oper", "-sfc")], online_h=7.75, trule=True),
    "ecmwf_aifs": EcmwfOpenData("ecmwf_aifs", "ECMWF AIFS single", [("aifs-single", "0p25", "oper", ""),
                                                                    ("aifs", "0p25", "oper", "")], online_h=6.0,
                                trule=True),
    "gfs": Gfs(),
    "hrrr": Hrrr(),
    "nbm": Nbm(),
    "ecmwf_ens": EcmwfEns(),
    # Open-Meteo's archive of the models it serves. online_h: hours after the
    # cycle until every domain of the run is in the archive (the largest
    # delay seen over 2026-11-15/18 runs, rounded up)
    "om_icon": OpenMeteoArchive("om_icon", "DWD ICON (D2, EU, global)", ["dwd_icon_d2", "dwd_icon_eu", "dwd_icon"],
                                online_h=5.0),
    # No HRDPS West: its stored snowfall_water_equivalent is not snowfall (it runs
    # 50-200x the run's precipitation and grows through the season), and the
    # API's gem_seamless leaves that domain out too
    "om_gem": OpenMeteoArchive("om_gem", "ECCC GEM (HRDPS, RDPS, GDPS)",
                               ["cmc_gem_hrdps", "cmc_gem_rdps_10km", "cmc_gem_gdps_15km"],
                               online_h=7.0, cycles=(0, 12)),
    "om_ukmo": OpenMeteoArchive("om_ukmo", "Met Office global", ["ukmo_global_deterministic_10km"], online_h=8.0),
    "om_meteofrance": OpenMeteoArchive("om_meteofrance", "Meteo-France AROME/ARPEGE",
                                       ["meteofrance_arome_france0025", "meteofrance_arpege_europe",
                                        "meteofrance_arpege_world025"], online_h=6.5),
    "om_jma": OpenMeteoArchive("om_jma", "JMA MSM/GSM", ["jma_msm", "jma_gsm"], online_h=10.0),
    "om_cma": OpenMeteoArchive("om_cma", "CMA GRAPES global", ["cma_grapes_global"], online_h=10.5),
    "om_icon_2i": OpenMeteoArchive("om_icon_2i", "ItaliaMeteo ICON-2I", ["italia_meteo_arpae_icon_2i"],
                                   online_h=4.5, cycles=(0, 12)),
    "om_meteoswiss": OpenMeteoArchive("om_meteoswiss", "MeteoSwiss ICON-CH1/CH2",
                                      ["meteoswiss_icon_ch1", "meteoswiss_icon_ch2"], online_h=3.5),
    "om_knmi": OpenMeteoArchive("om_knmi", "KNMI HARMONIE Europe", ["knmi_harmonie_arome_europe"], online_h=3.0,
                                cycles=tuple(range(0, 24, 3))),
    "om_dmi": OpenMeteoArchive("om_dmi", "DMI HARMONIE Europe", ["dmi_harmonie_arome_europe"], online_h=3.5,
                               cycles=tuple(range(0, 24, 3))),
    "om_metno": OpenMeteoArchive("om_metno", "MET Nordic", ["metno_nordic_pp"], online_h=1.0,
                                 cycles=tuple(range(0, 24, 3))),
    "om_rrfs": OpenMeteoArchive("om_rrfs", "NOAA RRFS", ["ncep_rrfs_conus"], online_h=9.5),
    "om_chmi": OpenMeteoArchive("om_chmi", "CHMI ALADIN Central Europe", ["chmi_aladin_central_europe_2km"],
                                online_h=6.5),
}

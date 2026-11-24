"""Météo-France — daily climatological data, snow at French weather stations.

"Données climatologiques de base - quotidiennes" (meteo.data.gouv.fr), one
file pair per département: `autres-parametres` carries the snow and
`RR-T-Vent` the precipitation. Licence Ouverte 2.0 (Etalab), no key: credit
"Météo-France".

Two kinds of station, told apart by the station id:
  - `<NUM_POSTE>:FR:MFHN`: a new-snow board, `HNEIGEF` (cm), the snow fallen
    in the 24 h from 06 UTC on the date. Day D <- the value dated D+k,
    k = day_offset (default 0). Over winter 2025-26, at the 69 stations with
    both elements, HNEIGEF dated D tracks the 06 UTC depth rise D -> D+1
    (median r 0.94), not D-1 -> D (0.27).
  - `<NUM_POSTE>:FR:MF`: depth only, `NEIGETOT06` (cm, read at 06 UTC). Day D
    <- max(depth(D+k) - depth(D+k-1), 0), k = day_offset (default 1, the
    06 UTC readings either side of day D), like SMHI.
    Drift check: a rise of DRIFT_RISE_CM or more is voided (QC_NO_SWE_SUPPORT)
    when the station's own gauge (RR, 06 UTC to 06 UTC, dated by its first
    day) caught under DRY_MM over the same 24 h. A missing gauge value
    voids nothing.

Quality: Météo-France flags each value (Q* columns: 0 protected, 1 validated,
2 doubtful, 9 not yet validated, which is every recent day). Doubtful (2)
values are not used.

Files: `previous-1950-<Y-2>` (history, regenerated monthly) and
`latest-<Y-1>-<Y>` (refreshed daily ~07:00 UTC, through two days back). The
names roll over each January, so last year's names are tried when this
year's are not up yet. History files are cached for good, the latest file at
most once per UTC day; both are reduced to the needed columns from 2005 on.
"""

from __future__ import annotations

import gzip
import io
import logging
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

BASE = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/QUOT/"
IN_PER_CM = 1 / 2.54
DRIFT_RISE_CM = 10.0
DRY_MM = 0.5
DOUBTFUL = 2
FROM = 20050101
RETRIES = 5
PAUSE_S = 2.0  # the bucket throttles bursts (2026-11-20: 20 back-to-back GETs all failed)
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
SNOW = ["HNEIGEF", "QHNEIGEF", "NEIGETOT06", "QNEIGETOT06"]
PRECIP = ["RR", "QRR"]
COLUMNS = ["num_poste", "date", "hn_cm", "depth_cm", "rr_mm"]


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def kind(station_id: str) -> str:
    return "hn" if station_id.endswith(":MFHN") else "depth"


def department(num_poste: str) -> str:
    return num_poste[:2]


def _get(name: str) -> bytes | None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(BASE + name, headers=UA, timeout=300)
            if r.status_code in (403, 404):
                return None
            if r.status_code >= 500 or r.status_code in (429, 503):
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            if r.content[:2] != b"\x1f\x8b":
                raise requests.HTTPError("not gzip (throttled?)")
            time.sleep(PAUSE_S)
            return r.content
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(10 * 2**attempt, 120)
            log.warning("Météo-France request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def parse(raw: bytes, cols: list[str]) -> pd.DataFrame:
    """NUM_POSTE, AAAAMMJJ + cols, from 2005 on."""
    frames = []
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8") as f:
        for chunk in pd.read_csv(f, sep=";", usecols=["NUM_POSTE", "AAAAMMJJ", *cols], dtype={"NUM_POSTE": str},
                                 chunksize=500_000):
            frames.append(chunk[chunk["AAAAMMJJ"] >= FROM])
    return pd.concat(frames, ignore_index=True)


def reduce(snow: pd.DataFrame, rain: pd.DataFrame) -> pd.DataFrame:
    """-> num_poste, date, hn_cm, depth_cm, rr_mm with doubtful values blanked."""
    s = snow.copy()
    s.loc[s["QHNEIGEF"] == DOUBTFUL, "HNEIGEF"] = float("nan")
    s.loc[s["QNEIGETOT06"] == DOUBTFUL, "NEIGETOT06"] = float("nan")
    s = s.dropna(subset=["HNEIGEF", "NEIGETOT06"], how="all")
    r = rain.copy()
    r.loc[r["QRR"] == DOUBTFUL, "RR"] = float("nan")
    out = s[["NUM_POSTE", "AAAAMMJJ", "HNEIGEF", "NEIGETOT06"]].merge(
        r[["NUM_POSTE", "AAAAMMJJ", "RR"]].dropna(), on=["NUM_POSTE", "AAAAMMJJ"], how="left")
    out["date"] = pd.to_datetime(out["AAAAMMJJ"].astype(str), format="%Y%m%d").dt.date
    out = out.rename(columns={"NUM_POSTE": "num_poste", "HNEIGEF": "hn_cm", "NEIGETOT06": "depth_cm",
                              "RR": "rr_mm"})
    return out[COLUMNS]


def _names(dep: str, period: str, year: int) -> list[str]:
    if period == "latest":
        return [f"Q_{dep}_latest-{y - 1}-{y}_{{}}.csv.gz" for y in (year, year - 1)]
    return [f"Q_{dep}_previous-1950-{y - 2}_{{}}.csv.gz" for y in (year, year - 1)]


def _file(dep: str, period: str) -> pd.DataFrame:
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "meteofrance" / dep
    for pattern in _names(dep, period, today.year):
        stem = pattern.format("snow").replace(".csv.gz", "")
        path = root / (f"{stem}-{today:%Y%m%d}.csv.gz" if period == "latest" else f"{stem}.csv.gz")
        if path.exists():
            df = pd.read_csv(path, dtype={"num_poste": str})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        snow = _get(pattern.format("autres-parametres"))
        if snow is None:
            continue
        rain = _get(pattern.format("RR-T-Vent"))
        df = reduce(parse(snow, SNOW), parse(rain, PRECIP) if rain else pd.DataFrame(
            columns=["NUM_POSTE", "AAAAMMJJ", *PRECIP]))
        root.mkdir(parents=True, exist_ok=True)
        if period == "latest":
            for old in root.glob(f"Q_{dep}_latest-*.csv.gz"):
                old.unlink()
        df.to_csv(path, index=False)
        return df
    raise RuntimeError(f"Météo-France: no {period} file for département {dep}")


@lru_cache(maxsize=32)
def _department(dep: str, day: date) -> pd.DataFrame:
    # `day` keys the in-process cache to the UTC day the latest file is for
    df = pd.concat([_file(dep, "previous"), _file(dep, "latest")], ignore_index=True)
    return df.drop_duplicates(["num_poste", "date"], keep="last")


def record(num_poste: str) -> pd.DataFrame:
    """date-indexed hn_cm, depth_cm, rr_mm for one station."""
    df = _department(department(num_poste), datetime.now(timezone.utc).date())
    return df[df["num_poste"] == num_poste].set_index("date").sort_index()


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end]."""
    rows = []
    offsets = offsets or {}
    for sid in station_ids:
        rec = record(code(sid))
        hn, dep, rr = rec["hn_cm"], rec["depth_cm"], rec["rr_mm"]
        hn_station = kind(sid) == "hn"
        k = offsets.get(sid, 0 if hn_station else 1)
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            flag, snow = truth.QC_OK, float("nan")
            if hn_station:
                v = hn.get(d + timedelta(days=k))
                if v is None or pd.isna(v):
                    flag = truth.QC_MISSING
                elif v < 0:
                    flag = truth.QC_BAD_DEPTH
                elif v * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
                    flag = truth.QC_IMPLAUSIBLE
                else:
                    snow = v * IN_PER_CM
            else:
                d0, d1 = d + timedelta(days=k - 1), d + timedelta(days=k)
                s0, s1, p = dep.get(d0), dep.get(d1), rr.get(d0)  # RR dated d0 = 06 UTC d0 .. d1
                if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
                    flag = truth.QC_MISSING
                elif s0 < 0 or s1 < 0 or s1 * IN_PER_CM > truth.MAX_DEPTH_IN:
                    flag = truth.QC_BAD_DEPTH
                elif (s1 - s0) * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
                    flag = truth.QC_IMPLAUSIBLE
                elif s1 - s0 >= DRIFT_RISE_CM and p is not None and not pd.isna(p) and p < DRY_MM:
                    flag = truth.QC_NO_SWE_SUPPORT
                else:
                    snow = max((s1 - s0) * IN_PER_CM, 0.0)
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": round(snow, 3) if valid else snow,
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

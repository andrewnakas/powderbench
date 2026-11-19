"""Land Tirol — automatic snow-depth stations of the avalanche warning service
(LWD Tirol) and the hydrographic service (HD Tirol), Tyrolean Alps.

Many LWD stations stand on ski areas (Sölden's Rettenbach glacier and
Panorama reservoir, Pitztal glacier, Ischgl, St. Anton, Obergurgl, Kühtai…).
Data: "Wetterstationsdaten Tirol" (data.gv.at), Land Tirol, CC BY 4.0, credit
"Land Tirol — data.tirol.gv.at". The station files are the SMET files behind
lawinen.report:

  - `winter/<id>.smet.gz`: every 10-minute reading of the last 12 months
  - `all/<id>.smet.gz`: the full record (1990s on) as 24-hour MEANS, stamped
    at the end of each local day (23:00 UTC)

New snow is derived from depth like SNOTEL: the depth at the start of local
day D is the median of the 10-minute readings from 22:40 to 23:20 UTC on D-1
(local midnight, CET), and day D <- max(depth(D+1) - depth(D), 0); rises under
DEADBAND_CM count as 0, so sensor jitter is not scored as snow. Where no
10-minute data exist (older than 12 months: climatology only) the daily means
are used instead, which spread a storm over two days but keep its total.

Station ids are `<id>:AT:LWDT` (the LWD number, e.g. SRET2). Both files are
fetched at most once per UTC day per station; only the derived midnight
depths are cached.
"""

from __future__ import annotations

import gzip
import io
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

BASE = "https://wiski.tirol.gv.at/lawine/grafiken/smet/{kind}/{sid}.smet.gz"
CM_PER_IN = 2.54
DEADBAND_CM = 1.0
MIDNIGHT_UTC_H = 23  # local midnight in winter (CET)
WINDOW_MIN = 20
RETRIES = 5
PAUSE_S = 0.5
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def parse_smet(text: str) -> pd.DataFrame:
    """timestamp (UTC), hs_cm from one SMET file (nodata and negatives -> NaN)."""
    head, _, body = text.partition("[DATA]")
    meta = {}
    for line in head.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            meta[k.strip()] = v.strip()
    fields = meta.get("fields", "timestamp HS").split()
    nodata = float(meta.get("nodata", "-777"))
    if "HS" not in fields:
        return pd.DataFrame(columns=["timestamp", "hs_cm"])
    df = pd.read_csv(io.StringIO(body.replace("[DATA]", "")), sep=r"\s+", header=None, names=fields,
                     usecols=["timestamp", "HS"], comment="#")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    hs = pd.to_numeric(df["HS"], errors="coerce")
    df["hs_cm"] = (hs.where((hs != nodata) & (hs >= 0)) * 100).round(1)
    return df.dropna(subset=["timestamp"])[["timestamp", "hs_cm"]].drop_duplicates("timestamp")


def midnight_depths(df: pd.DataFrame) -> pd.Series:
    """10-minute readings -> depth (cm) at the start of each local day."""
    t = df["timestamp"]
    shifted = t + pd.Timedelta(hours=24 - MIDNIGHT_UTC_H)  # 23:00 UTC D-1 -> 00:00 D
    off = (shifted - shifted.dt.floor("D")).dt.total_seconds() / 60
    near = (off <= WINDOW_MIN) | (off >= 24 * 60 - WINDOW_MIN)
    day = (shifted + pd.Timedelta(minutes=WINDOW_MIN)).dt.floor("D").dt.date
    s = pd.Series(df["hs_cm"].to_numpy(), index=day.to_numpy())[near.to_numpy()]
    return s.dropna().groupby(level=0).median()


def daily_means(df: pd.DataFrame) -> pd.Series:
    """`all` file: the 24 h mean stamped 23:00 UTC on D-1 is used as the depth
    at the start of day D (the window it averages ends there)."""
    day = (df["timestamp"] + pd.Timedelta(hours=24 - MIDNIGHT_UTC_H)).dt.date
    return pd.Series(df["hs_cm"].to_numpy(), index=day.to_numpy()).dropna()


def _fetch(kind: str, sid: str) -> str:
    for attempt in range(RETRIES):
        try:
            r = requests.get(BASE.format(kind=kind, sid=sid), headers=UA, timeout=180)
            if r.status_code == 404:
                return ""
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            body = r.content
            return gzip.decompress(body).decode("utf-8", "replace") if body[:2] == b"\x1f\x8b" else body.decode()
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("LWD Tirol request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _depths(kind: str, sid: str) -> pd.Series:
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "lwdtirol" / sid
    path = root / f"{kind}-{today:%Y%m%d}.csv"
    if path.exists() and path.stat().st_size > 0:
        df = pd.read_csv(path)
        return pd.Series(df["depth_cm"].to_numpy(), index=pd.to_datetime(df["date"]).dt.date)
    text = _fetch(kind, sid)
    raw = parse_smet(text) if text else pd.DataFrame(columns=["timestamp", "hs_cm"])
    s = (midnight_depths(raw) if kind == "winter" else daily_means(raw)) if len(raw) else pd.Series(dtype=float)
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(f"{kind}-*.csv"):
        old.unlink()
    pd.DataFrame({"date": list(s.index), "depth_cm": s.to_numpy()}).to_csv(path, index=False)
    return s


def depths(sid: str, begin: date) -> pd.Series:
    """Midnight depths from the 10-minute file, with the daily-mean record
    filling days before it (only fetched when the range needs them)."""
    fine = _depths("winter", sid)
    if len(fine) and begin >= min(fine.index):
        return fine
    coarse = _depths("all", sid)
    if not len(fine):
        return coarse
    return pd.concat([coarse[coarse.index < min(fine.index)], fine])


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from midnight-to-midnight depth rises."""
    rows = []
    for sid in station_ids:
        rows += rise_rows(sid, depths(code(sid), begin), begin, end)
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])


def rise_rows(sid: str, rec: pd.Series, begin: date, end: date) -> list[dict]:
    """Day D <- depth(D+1) - depth(D) (midnight depths by date), with the
    deadband and the shared depth QC. Used by every depth-derived network."""
    rows = []
    for i in range((end - begin).days + 1):
        d = begin + timedelta(days=i)
        s0, s1 = rec.get(d), rec.get(d + timedelta(days=1))
        flag = truth.QC_OK
        if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
            flag = truth.QC_MISSING
        elif s1 / CM_PER_IN > truth.MAX_DEPTH_IN:
            flag = truth.QC_BAD_DEPTH
        elif (s1 - s0) / CM_PER_IN > truth.MAX_PLAUSIBLE_SNOW24_IN:
            flag = truth.QC_IMPLAUSIBLE
        valid = flag == truth.QC_OK
        rise = (s1 - s0) if valid else float("nan")
        snow = (0.0 if rise < DEADBAND_CM else round(rise / CM_PER_IN, 3)) if valid else float("nan")
        rows.append({"station_id": sid, "date": d, "snow24": snow, "valid": valid, "qc_flag": flag})
    return rows

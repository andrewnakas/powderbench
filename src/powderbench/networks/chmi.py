"""ČHMÚ (Czech Hydrometeorological Institute) — new snow measured by hand at
the morning reading of Czech climate stations (Krkonoše, Jeseníky, Šumava,
Krušné hory, Beskydy).

Element `SNO` (nový sníh, cm) of ČHMÚ's open climate data: the new snow an
observer reads at 06 UTC (07 CET), filed under the day the snow fell: the
value dated D tracks the station's own morning depth `SCE` rising from D to
D+1 (offset 0; a few stations correlate better with D-1 -> D and would take
offset 1). The offset is fitted per station by the generator and stored as
`day_offset`; day D <- the value dated D+offset. Open
data, no key, CC BY 4.0: credit "ČHMÚ" (https://opendata.chmi.cz).

Before the month is closed, some stations file SNO only on mornings with new
snow (Klínovec, Desná; the rest blank, QUALITY 4) while the depth `SCE` is
filed every morning: a blank SNO whose next-morning depth reads 0 cm is 0
(nothing fell, or nothing lay), a blank under a standing pack stays missing.

Flags: `A` = the reading is affected by artificial snow (snowmaking) -> not
used; `P` (a dusting, value 0), `N` (patchy) and `R` (fell and melted) are
kept as the observer filed them. QUALITY >= 3 (doubtful, missing) -> not used.

Data: per station, the historical CSV (the whole record to the end of last
year, re-read once a year) and one JSON per month since then (the current
month and the RECHECK_MONTHS before it re-read at most once per UTC day,
earlier months once).

Latency: the professional stations' readings are online the next morning.
The volunteer climate observers' arrive with the monthly report, six to eight
weeks late (on 2026-11-19 October was still blank, QUALITY 4), too late for a
league that resolves at D+6, so the generator keeps only stations whose
readings are current.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

BASE = "https://opendata.chmi.cz/meteorology/climate"
HIST = BASE + "/historical_csv/data/daily/snow/dly-{wsi}-{el}.csv"
MONTH_NOW = BASE + "/recent/data/daily/dly-{wsi}-{ym}.json"
MONTH_PAST = BASE + "/recent/data/daily/{mm}/dly-{wsi}-{ym}.json"
CM_PER_IN = 2.54
DEFAULT_OFFSET = 0  # all 22 kept stations fit 0 (2026-11)
BAD_FLAGS = {"A"}
MAX_QUALITY = 2.0
RETRIES = 5
PAUSE_S = 1.0
RECHECK_MONTHS = 3  # late monthly reports and corrections
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    """'0-20000-0-11643:CZ:CHMI' -> the WIGOS station id."""
    return station_id.split(":")[0]


def _get(url: str) -> bytes | None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=180)
            if r.status_code == 404:
                return None
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.content
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("CHMI request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """element, date, value (cm) from rows STATION, ELEMENT, *, DT, VALUE|VAL, FLAG, QUALITY."""
    val = "VALUE" if "VALUE" in df else "VAL"
    t = pd.to_datetime(df["DT"], errors="coerce", utc=True)
    flag = df["FLAG"].astype("string").fillna("").str.strip()
    q = pd.to_numeric(df["QUALITY"], errors="coerce").fillna(0)
    v = pd.to_numeric(df[val], errors="coerce")
    ok = t.notna() & v.notna() & (v >= 0) & ~flag.isin(BAD_FLAGS) & (q <= MAX_QUALITY)
    return pd.DataFrame({"element": df["ELEMENT"][ok].to_numpy(), "date": t[ok].dt.date.to_numpy(),
                         "value": v[ok].to_numpy(float)})


def parse_csv(blob: bytes) -> pd.DataFrame:
    return _clean(pd.read_csv(io.BytesIO(blob), dtype=str))


def parse_json(blob: bytes) -> pd.DataFrame:
    d = json.loads(blob)["data"]["data"]
    return _clean(pd.DataFrame(d["values"], columns=d["header"].split(",")).astype(str))


def _cached(path, fetch) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    df = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def record(wsi: str, elements: tuple[str, ...] = ("SNO", "SCE")) -> pd.DataFrame:
    """date-indexed columns per element (cm): the historical CSVs + the months since."""
    today = datetime.now(timezone.utc).date()
    root = data_dir() / "cache" / "chmi" / wsi
    empty = pd.DataFrame(columns=["element", "date", "value"])
    parts = []
    for el in elements:
        path = root / f"hist-{el}-{today.year}.csv"
        if not path.exists():
            for old in root.glob(f"hist-{el}-*.csv"):
                old.unlink()
        parts.append(_cached(path, lambda el=el: (lambda b: parse_csv(b) if b else empty)(
            _get(HIST.format(wsi=wsi, el=el)))))
    hist = pd.concat(parts, ignore_index=True)
    last = max(hist["date"]) if len(hist) else date(today.year - 1, 12, 31)
    m = date(last.year, last.month, 1) + timedelta(days=32)
    m = max(date(m.year, m.month, 1), date(today.year - 1, 1, 1))  # a closed station: no month walk
    while m <= today:
        ym = f"{m:%Y%m}"
        current = (m.year, m.month) == (today.year, today.month)
        recent = (today.year - m.year) * 12 + today.month - m.month <= RECHECK_MONTHS
        name = f"m-{ym}-{today:%Y%m%d}.csv" if recent else f"m-{ym}.csv"
        for old in [*root.glob(f"m-{ym}-*.csv"), root / f"m-{ym}.csv"]:
            if old.exists() and old.name != name:
                old.unlink()
        url = MONTH_NOW.format(wsi=wsi, ym=ym) if current else MONTH_PAST.format(mm=f"{m:%m}", wsi=wsi, ym=ym)
        parts.append(_cached(root / name, lambda url=url: (lambda b: parse_json(b) if b else empty)(_get(url))))
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    df = pd.concat(parts, ignore_index=True)
    df = df[df["element"].isin(elements)]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    wide = df.drop_duplicates(["element", "date"], keep="last").pivot(index="date", columns="element", values="value")
    return wide.reindex(columns=list(elements))


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end]: day D <- the new snow dated D+offset."""
    offsets = offsets or {}
    rows = []
    for sid in station_ids:
        rec = record(code(sid))
        sno, sce = rec["SNO"], rec["SCE"]
        k = offsets.get(sid, DEFAULT_OFFSET)
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            cm = sno.get(d + timedelta(days=k))
            if (cm is None or pd.isna(cm)) and sce.get(d + timedelta(days=k + 1)) == 0:
                cm = 0.0  # bare the next morning: nothing new to report
            flag = truth.QC_OK
            if cm is None or pd.isna(cm):
                flag = truth.QC_MISSING
            elif cm / CM_PER_IN > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": round(cm / CM_PER_IN, 3) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

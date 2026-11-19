"""Meteotrentino — manual snow fields (campi neve), Trentino, Italian Alps.

Truth is HN, the measured 24-hour new snow (cm) read off a snow board at about
08:00 local time by observers at Meteotrentino's snow fields (AINEVA "Modello 1"
observations: Passo Tonale, Presena, Grostè/Madonna di Campiglio, Folgarida,
Pejo, Canazei, Pampeago, Passo Rolle, San Pellegrino, Monte Bondone, ...).

Source: Meteotrentino's open web service (dati.meteotrentino.it, also listed on
dati.trentino.it as "Dati recenti dei campi neve"). Licence CC BY 4.0, credit
"Provincia Autonoma di Trento — Meteotrentino". One request per station-season
(`rilieviNeveStagionaliUnaStazioneJson`, seasons run Sep-Aug and include this
morning's reading); past seasons are cached, the current one is re-fetched at
most once per UTC day.

HN read on the morning of day D+1 is the snow of day D, so day D <- the value
dated D+1 (day_offset 1, as SLF and DWD). HN is read from the raw AINEVA
code (`000`, `0` = none, `015` = 15 cm); `///` is missing. A reading that
covers more than one day (`CheckCum` / `HnCum`, after a day without an
observer) is not one day's snow and reads missing.

Station ids are `<codice>:IT:METEOTN`; `external_id` is the field code.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

API = "https://dati.meteotrentino.it/service.asmx/"
CM_PER_IN = 2.54
DEFAULT_OFFSET = 1
RETRIES = 5
PAUSE_S = 0.3
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def season_of(d: date) -> int:
    """Meteotrentino seasons run 1 Sep - 31 Aug and are named by the first year."""
    return d.year if d.month >= 9 else d.year - 1


def _get(op: str, **params) -> dict:
    for attempt in range(RETRIES):
        try:
            r = requests.get(API + op, params=params, headers=UA, timeout=120)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("Meteotrentino request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def parse_obs(obs: list[dict]) -> pd.DataFrame:
    """date, hn_cm (NaN = missing or multi-day) from one season's observations."""
    rows = []
    for r in obs:
        d = date.fromisoformat(str(r["DataRilievo"])[:10])
        # the raw AINEVA code, not HNnum: HNnum turns the code "0" (no new snow,
        # dry days with the depth falling) into 1 cm
        raw = str(r.get("HN") or "").strip()
        cum = bool(r.get("CheckCum")) or str(r.get("HnCum") or "").strip().isdigit()
        hn = float(raw) if raw.isdigit() and not cum else float("nan")
        rows.append({"date": d, "hn_cm": hn})
    df = pd.DataFrame(rows, columns=["date", "hn_cm"])
    return df.drop_duplicates("date", keep="last")


def _season(field: str, start_year: int) -> pd.DataFrame:
    today = datetime.now(timezone.utc).date()
    current = start_year >= season_of(today)
    root = data_dir() / "cache" / "meteotn" / field
    tag = f"{start_year}" + (f"-{today:%Y%m%d}" if current else "")
    path = root / f"{tag}.csv"
    if path.exists() and path.stat().st_size > 0:
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    if start_year > season_of(today):
        return pd.DataFrame(columns=["date", "hn_cm"])
    df = parse_obs(_get("rilieviNeveStagionaliUnaStazioneJson", stagione=start_year,
                        camponeve=field).get("osservazioni") or [])
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(f"{start_year}-*.csv"):  # yesterday's copy of the current season
        old.unlink()
    df.to_csv(path, index=False)
    return df


def record(field: str, begin: date, end: date) -> dict[date, float]:
    out: dict[date, float] = {}
    for y in range(season_of(begin), season_of(end) + 1):
        for r in _season(field, y).itertuples():
            out[r.date] = r.hn_cm
    return out


def daily(stations: dict[str, str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """stations: {station_id: field code}. Truth frame for local days [begin, end]:
    day D <- the HN dated D+offset (default 1)."""
    offsets = offsets or {}
    rows = []
    for sid, field in stations.items():
        off = offsets.get(sid, DEFAULT_OFFSET)
        rec = record(field, begin + timedelta(days=off), end + timedelta(days=off))
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            cm = rec.get(d + timedelta(days=off))
            flag = truth.QC_OK
            if cm is None or pd.isna(cm):
                flag = truth.QC_MISSING
            elif cm < 0 or cm / CM_PER_IN > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": round(cm / CM_PER_IN, 3) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

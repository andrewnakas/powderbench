"""ARPA Piemonte — validated daily snow depth (HS) read at 08:00 local time at
the automatic snow stations of Piedmont's network. CC BY 4.0: credit "Fonte:
Arpa Piemonte – www.arpa.piemonte.it" (https://www.arpa.piemonte.it/note-legali).

Two feeds, no key:
  - live: api_realtime/pie_neve, every station's validated 08:00 depth for the
    last 7 days (through yesterday), one request for all stations. Each day's
    response is kept, so days that have left the window stay available;
  - history: meteoidro/dati_giornalieri_nivo, daily hs (and hn) since 1990,
    validated about once a year (through 30 June of the last season).
Station codes map to the history's keys (PIE-<ISTAT municipality>-<point>)
through the registry, api_realtime/pie_anag.

New snow is derived from depth like the nordic networks: day D <-
max(hs(D+k) - hs(D+k-1), 0) with k = the station's day_offset (default 1: the
rise 08:00 D .. 08:00 D+1). There is no precipitation feed beside the depth,
so the shared depth rules are the only QC.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

LIVE = "https://utility.arpa.piemonte.it/api_realtime/pie_neve"
ANAG = "https://utility.arpa.piemonte.it/api_realtime/pie_anag"
HISTORY = "https://utility.arpa.piemonte.it/meteoidro/dati_giornalieri_nivo/"
HISTORY_FROM = date(2010, 10, 1)
HISTORY_TTL_DAYS = 30
PAGE_ROWS = 366
IN_PER_CM = 1 / 2.54
RETRIES = 5
PAUSE_S = 0.3
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def _get(url: str, params: dict | None = None):
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=120)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.json()
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("ARPA Piemonte request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _root():
    return data_dir() / "cache" / "arpapie"


def _today() -> date:
    return datetime.now(timezone.utc).date()


def registry() -> list[dict]:
    """The station registry (all networks), re-read at most once per UTC day."""
    path = _root() / f"anag-{_today():%Y%m%d}.json"
    if not path.exists():
        rows = _get(ANAG)
        path.parent.mkdir(parents=True, exist_ok=True)
        for old in path.parent.glob("anag-*.json"):
            old.unlink()
        path.write_text(json.dumps(rows))
    return json.loads(path.read_text())


def history_key(row: dict) -> str:
    return f"PIE-{row['codice_istat_comune']}-{row['progr_punto_com']}"


def live() -> pd.DataFrame:
    """station_code, date, hs_cm from every live response kept so far (today's fetched once)."""
    root = _root() / "live"
    path = root / f"{_today():%Y%m%d}.json"
    if not path.exists():
        rows = _get(LIVE)
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    out = []
    for p in sorted(root.glob("*.json")):  # older files first: a later response wins
        for r in json.loads(p.read_text()):
            out.append({"station_code": str(r["station_code"]),
                        "date": datetime.fromisoformat(r["date"]).date(),
                        "hs_cm": r.get("validated_snow_height")})
    df = pd.DataFrame(out, columns=["station_code", "date", "hs_cm"])
    return df.drop_duplicates(["station_code", "date"], keep="last")


def history(key: str) -> pd.DataFrame:
    """date, hs_cm since HISTORY_FROM for one history key (cached HISTORY_TTL_DAYS)."""
    path = _root() / "history" / f"{key}.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < HISTORY_TTL_DAYS * 86400
    if not fresh:
        params = {"format": "json", "fk_id_punto_misura_nivo": key}
        first = _get(HISTORY, dict(params, page=1))
        pages = max(1, math.ceil(first["count"] / PAGE_ROWS))
        rows = []
        for page in range(pages, 0, -1):  # newest page first, back to HISTORY_FROM
            res = first["results"] if page == 1 else _get(HISTORY, dict(params, page=page))["results"]
            rows += res
            if res and date.fromisoformat(res[0]["data"]) < HISTORY_FROM:
                break
        df = pd.DataFrame([{"date": r["data"], "hs_cm": r.get("hs")} for r in rows], columns=["date", "hs_cm"])
        df = df[df["date"] >= HISTORY_FROM.isoformat()].sort_values("date")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def depths(station_ids: list[str]) -> dict[str, pd.Series]:
    """station_id -> date-indexed 08:00 depth (cm): history, then live on top."""
    keys = {str(r["station_code"]): history_key(r) for r in registry() if r.get("id_network") == "PIE"}
    lv = live()
    out = {}
    for sid in station_ids:
        c = code(sid)
        parts = []
        if c in keys:
            parts.append(history(keys[c]).set_index("date")["hs_cm"])
        parts.append(lv[lv["station_code"] == c].set_index("date")["hs_cm"])
        s = pd.concat(parts)
        s = pd.to_numeric(s[~s.index.duplicated(keep="last")], errors="coerce").sort_index()
        out[sid] = s.where(s >= 0)
    return out


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from 08:00 depth deltas."""
    offsets = offsets or {}
    deps = depths(station_ids)
    rows = []
    for sid in station_ids:
        k = offsets.get(sid, 1)
        dep = deps[sid]
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            s0, s1 = dep.get(d + timedelta(days=k - 1)), dep.get(d + timedelta(days=k))
            flag = truth.QC_OK
            if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
                flag = truth.QC_MISSING
            elif s1 * IN_PER_CM > truth.MAX_DEPTH_IN:
                flag = truth.QC_BAD_DEPTH
            elif (s1 - s0) * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            snow = round(max((s1 - s0) * IN_PER_CM, 0.0), 3) if valid else float("nan")
            rows.append({"station_id": sid, "date": d, "snow24": snow, "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

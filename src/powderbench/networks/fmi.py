"""Finnish Meteorological Institute (FMI) — daily snow depth.

Parameter `snow` of FMI's daily observations (cm; -1 = no snow cover): the
morning depth read at 06 UTC (08:00 local). Open data via FMI's WFS, no key,
CC BY 4.0: credit "Finnish Meteorological Institute" (https://en.ilmatieteenlaitos.fi/open-data).

New snow is derived from depth as for SMHI: day D <- max(depth(D+1) -
depth(D), 0), the morning-to-morning rise. Station ids are
`<fmisid>:FI:FMI`.

Data: one WFS request per station per season (Oct-Sep, WFS caps a daily
query near a year). Past seasons are cached; the current one is re-fetched at
most once per UTC day.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

WFS = "https://opendata.fmi.fi/wfs"
QUERY = "fmi::observations::weather::daily::simple"
IN_PER_CM = 1 / 2.54
RETRIES = 5
PAUSE_S = 0.3
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
_MEMBER = re.compile(r"<BsWfs:Time>([^<]+)</BsWfs:Time>.*?<BsWfs:ParameterValue>([^<]*)</BsWfs:ParameterValue>",
                     re.S)


def code(station_id: str) -> str:
    return station_id.split(":")[0]


def parse_wfs(xml: str) -> pd.DataFrame:
    """date, depth_cm (NaN for missing; -1 'no cover' -> 0)."""
    rows = []
    for t, v in _MEMBER.findall(xml):
        try:
            val = float(v)
        except ValueError:
            val = float("nan")
        if val == -1:
            val = 0.0
        rows.append({"date": datetime.fromisoformat(t.replace("Z", "+00:00")).date(), "depth_cm": val})
    return pd.DataFrame(rows, columns=["date", "depth_cm"])


def _get(fmisid: str, begin: date, end: date) -> str:
    params = {"service": "WFS", "version": "2.0.0", "request": "getFeature", "storedquery_id": QUERY,
              "fmisid": fmisid, "parameters": "snow", "starttime": f"{begin}T00:00:00Z",
              "endtime": f"{end}T00:00:00Z"}
    for attempt in range(RETRIES):
        try:
            r = requests.get(WFS, params=params, headers=UA, timeout=180)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            time.sleep(PAUSE_S)
            return r.text
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("FMI request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _season(fmisid: str, start_year: int) -> pd.DataFrame:
    """Depths for Oct 1 start_year .. Sep 30 start_year+1 (to today at most)."""
    today = datetime.now(timezone.utc).date()
    begin, end = date(start_year, 10, 1), min(date(start_year + 1, 9, 30), today)
    current = end == today
    root = data_dir() / "cache" / "fmi" / fmisid
    tag = f"{start_year}" + (f"-{today:%Y%m%d}" if current else "")
    path = root / f"{tag}.csv"
    if path.exists():
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    if begin > today:
        return pd.DataFrame(columns=["date", "depth_cm"])
    df = parse_wfs(_get(fmisid, begin, end))
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(f"{start_year}-*.csv"):
        old.unlink()
    df.to_csv(path, index=False)
    return df


def depths(fmisid: str, begin: date, end: date) -> pd.Series:
    first = begin.year if begin.month >= 10 else begin.year - 1
    last = end.year if end.month >= 10 else end.year - 1
    df = pd.concat([_season(fmisid, y) for y in range(first, last + 1)], ignore_index=True)
    return df.drop_duplicates("date", keep="last").set_index("date")["depth_cm"]


def daily(station_ids: list[str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for local days [begin, end] from morning depth deltas."""
    rows = []
    for sid in station_ids:
        rec = depths(code(sid), begin, end + timedelta(days=1))
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            s0, s1 = rec.get(d), rec.get(d + timedelta(days=1))
            flag = truth.QC_OK
            if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
                flag = truth.QC_MISSING
            elif s0 < 0 or s1 < 0 or s1 * IN_PER_CM > truth.MAX_DEPTH_IN:
                flag = truth.QC_BAD_DEPTH
            elif (s1 - s0) * IN_PER_CM > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            snow = round(max((s1 - s0) * IN_PER_CM, 0.0), 3) if valid else float("nan")
            rows.append({"station_id": sid, "date": d, "snow24": snow, "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

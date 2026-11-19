"""Japan Meteorological Agency (JMA) — daily snowfall at AMeDAS stations.

Truth is 降雪の深さの合計 ("total depth of new snowfall", cm): JMA sums the
hourly increases of the automatic snow-depth sensor over the JST calendar day.
It is derived from a depth sensor, not a snowboard, so settling snow reads a
little low on long storms; every team is scored against the same number.

Source: JMA's past-weather archive (気象庁 過去の気象データ検索), one page per
station-month, preliminary values for yesterday by the next morning. Terms:
JMA website terms of use (compatible with CC BY 4.0); credit
"出典：気象庁ホームページ (Japan Meteorological Agency)".

Station ids are `<amedas code>:JP:JMA`; `external_id` is `<kind>:<prec_no>:<block_no>`
(kind `a` = AMeDAS page layout, `s` = staffed observatory layout).

Value marks in the archive: a trailing `)` is "quasi-normal" (a few hourly
values missing, still accepted by JMA) and is kept; `]` (insufficient data),
`#` (suspect), `×`, `///` and blanks are missing; `--` means no phenomenon = 0.

Days are JST calendar days, the stations' local day, so day D <- the value
dated D (offset 0). Past months are cached per station; the current month is
re-fetched at most once per UTC day.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .. import truth
from ..stations import data_dir

log = logging.getLogger(__name__)

URL = "https://www.data.jma.go.jp/stats/etrn/view/daily_{kind}1.php"
CM_PER_IN = 2.54
RETRIES = 5
PAUSE_S = 0.4  # be polite: one page at a time
# Jun-Sep are snow-free at every kept station: never fetched (days read missing)
SUMMER = {6, 7, 8, 9}
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
# column of the day's snowfall total in a data row, per page layout
SNOW_COL = {"a": 16, "s": 17}
ROW_LEN = {"a": 18, "s": 21}

_ROW = re.compile(r'<tr class="mtx"[^>]*>(.*?)</tr>', re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG = re.compile(r"<[^>]+>")


def parse_value(raw: str) -> float | None:
    """One archive cell -> cm (None = missing)."""
    v = raw.strip()
    if v == "--":
        return 0.0
    if not v or v.endswith("]") or "#" in v or "×" in v or "/" in v:
        return None
    v = v.rstrip(")").strip()
    try:
        return float(v)
    except ValueError:
        return None


def parse_month(page: str, kind: str) -> dict[int, float | None]:
    """{day of month: snowfall cm or None} from one daily_*1.php page."""
    out: dict[int, float | None] = {}
    for row in _ROW.findall(page):
        cells = [html.unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(row)]
        if len(cells) != ROW_LEN[kind] or not cells[0].isdigit():
            continue
        out[int(cells[0])] = parse_value(cells[SNOW_COL[kind]])
    return out


def _get(kind: str, prec: str, block: str, year: int, month: int) -> str:
    params = {"prec_no": prec, "block_no": block, "year": year, "month": month, "day": "", "view": ""}
    for attempt in range(RETRIES):
        try:
            r = requests.get(URL.format(kind=kind), params=params, headers=UA, timeout=60)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            r.encoding = "utf-8"
            time.sleep(PAUSE_S)
            return r.text
        except requests.RequestException as exc:
            if attempt == RETRIES - 1:
                raise
            wait = min(5 * 2**attempt, 60)
            log.warning("JMA request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _month(external_id: str, year: int, month: int) -> dict[int, float | None]:
    kind, prec, block = external_id.split(":")
    today = datetime.now(timezone.utc).date()
    current = (year, month) >= (today.year, today.month)
    root = data_dir() / "cache" / "jma" / f"{prec}-{block}"
    tag = f"{year}-{month:02d}" + (f"-{today:%Y%m%d}" if current else "")
    path = root / f"{tag}.csv"
    if path.exists():
        df = pd.read_csv(path)
        return {int(d): (None if pd.isna(v) else float(v)) for d, v in zip(df["day"], df["snow_cm"])}
    vals = parse_month(_get(kind, prec, block, year, month), kind)
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(f"{year}-{month:02d}-*.csv"):  # yesterday's copy of the current month
        old.unlink()
    pd.DataFrame({"day": list(vals), "snow_cm": list(vals.values())}).to_csv(path, index=False)
    return vals


def _months(begin: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], begin.year, begin.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_snow(stations: dict[str, str], begin: date, end: date) -> pd.DataFrame:
    """stations: {station_id: external_id}. Raw daily snowfall (inches) for
    JST days [begin, end]: station_id, date, snow_in (NaN = no value)."""
    rows = []
    for sid, ext in stations.items():
        for y, m in _months(begin, end):
            if m in SUMMER:
                continue
            for day, cm in _month(ext, y, m).items():
                d = date(y, m, day)
                if begin <= d <= end:
                    rows.append({"station_id": sid, "date": d,
                                 "snow_in": float("nan") if cm is None else cm / CM_PER_IN})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow_in"])


def daily(stations: dict[str, str], begin: date, end: date, offsets: dict[str, int] | None = None) -> pd.DataFrame:
    """Truth frame for JST days [begin, end]: day D <- the value dated D+offset (default 0)."""
    offsets = offsets or {}
    lo = min([0, *offsets.values()])
    hi = max([0, *offsets.values()])
    raw = fetch_snow(stations, begin + timedelta(days=lo), end + timedelta(days=hi))
    have = {(r.station_id, r.date - timedelta(days=offsets.get(r.station_id, 0))): r.snow_in
            for r in raw.itertuples()}
    rows = []
    for sid in stations:
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            snow = have.get((sid, d))
            flag = truth.QC_OK
            if snow is None or pd.isna(snow):
                flag = truth.QC_MISSING
            elif snow < 0 or snow > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": float(snow) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

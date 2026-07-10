"""Snapshot archive for resort snow reports.

Resort "24h snowfall" numbers are ephemeral — tomorrow the site shows a new
number and yesterday's is gone. So a twice-daily scrape appends immutable
snapshot CSVs under data/resortreports/raw/, and BOTH the resorts-league
truth source and the reference obs feed read from this archive, never the
live site.

Attribution: a report scraped between one morning refresh and the next
describes snow that fell on the PREVIOUS local calendar day (end-of-day
convention, same as SNOTEL periodRef=END):

    attributed = (local_scrape_time - report_hour_local hours).date() - 1 day

Scraping twice a day is harmless: rows are deduped read-side, keeping the
EARLIEST ok scrape per (station, attributed_date) — the one closest after the
morning refresh. (Later scrapes of live-updating cumulative sources already
include the next day's accumulation, so first-wins is the clean end-of-day
value.) Cumulative sources (kind=season_total) yield daily snowfall as the
day-over-day delta, requiring the previous attributed day to be present.

Failed scrapes are recorded as status rows too — the archive doubles as the
public audit trail behind `missing` QC voids.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ..stations import data_dir
from .registry import REGISTRY
from .spec import ResortSpec

log = logging.getLogger(__name__)

RAW_COLUMNS = [
    "station_id", "resort_id", "scraped_utc", "attributed_date",
    "value_raw", "unit", "value_in", "kind", "status",
]

KIND_SNOW24 = "snow24"
KIND_SEASON_TOTAL = "season_total"

STATUS_OK = "ok"
STATUS_NO_REPORT = "no_report"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_HTTP_ERROR = "http_error"
STATUS_ROBOTS_BLOCKED = "robots_blocked"

_STEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T\d{4}Z$")


def raw_dir() -> Path:
    return data_dir() / "resortreports" / "raw"


def attributed_date(scraped_utc: datetime, spec: ResortSpec) -> date:
    local = scraped_utc.astimezone(ZoneInfo(spec.tz))
    return (local - timedelta(hours=spec.report_hour_local)).date() - timedelta(days=1)


def scrape_all(now_utc: datetime | None = None, only: str | None = None) -> Path:
    """Fetch every enabled resort spec and write one snapshot CSV. One row per
    resort, failures included; a single bad site never blocks the run."""
    from .http import polite_get, robots_allowed

    now_utc = now_utc or datetime.now(timezone.utc)
    rows = []
    for spec in REGISTRY:
        if not spec.enabled or (only and spec.resort_id != only):
            continue
        # status starts at the failure mode of the *next* step, so an exception
        # anywhere leaves the row telling us how far the scrape got
        row = {
            "station_id": spec.station_id,
            "resort_id": spec.resort_id,
            "scraped_utc": now_utc.isoformat(timespec="seconds"),
            "attributed_date": attributed_date(now_utc, spec).isoformat(),
            "value_raw": "",
            "unit": spec.unit,
            "value_in": float("nan"),
            "kind": KIND_SEASON_TOTAL if spec.cumulative else KIND_SNOW24,
            "status": STATUS_HTTP_ERROR,
        }
        try:
            if not robots_allowed(spec.url):
                row["status"] = STATUS_ROBOTS_BLOCKED
            else:
                resp = polite_get(spec.url, headers=dict(spec.headers) if spec.headers else None)
                row["status"] = STATUS_PARSE_FAILED
                value = spec.parse(resp.text)
                if value is None:
                    row["status"] = STATUS_NO_REPORT
                else:
                    row["value_raw"] = str(value)
                    row["value_in"] = spec.to_inches(float(value))
                    row["status"] = STATUS_OK
        except Exception:
            log.warning("resort scrape failed: %s (%s)", spec.resort_id, row["status"], exc_info=True)
        rows.append(row)

    raw_dir().mkdir(parents=True, exist_ok=True)
    path = raw_dir() / f"{now_utc.strftime('%Y-%m-%dT%H%MZ')}.csv"
    pd.DataFrame(rows, columns=RAW_COLUMNS).to_csv(path, index=False)
    return path


def _dedup(begin: date, end: date, station_ids: list[str] | None) -> pd.DataFrame:
    """Ok rows in [begin, end], first scrape per (station, attributed_date)."""
    frames = []
    for path in sorted(raw_dir().glob("*.csv")):
        m = _STEM_RE.match(path.stem)
        if not m:
            continue
        # a snapshot on UTC day S only carries attributed dates in [S-2, S]
        snap_day = date.fromisoformat(m.group(1))
        if not (begin <= snap_day <= end + timedelta(days=3)):
            continue
        frames.append(pd.read_csv(path))
    if not frames:
        return pd.DataFrame(columns=RAW_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["status"] == STATUS_OK].copy()
    df["date"] = pd.to_datetime(df["attributed_date"]).dt.date
    df = df[(df["date"] >= begin) & (df["date"] <= end)]
    if station_ids is not None:
        df = df[df["station_id"].isin(set(station_ids))]
    return df.sort_values("scraped_utc").groupby(["station_id", "date"], as_index=False).first()


def daily_from_archive(begin: date, end: date, station_ids: list[str] | None = None) -> pd.DataFrame:
    """Deduped daily snowfall from the snapshot archive for [begin, end].

    Returns columns: station_id, date, snow24_in, scraped_utc. Cumulative
    stations need the previous attributed day on record — days after a gap
    are absent (they become `missing` QC voids at truth time).
    """
    # cumulative deltas for `begin` need the day before on hand
    df = _dedup(begin - timedelta(days=1), end, station_ids)
    if not len(df):
        return pd.DataFrame(columns=["station_id", "date", "snow24_in", "scraped_utc"])

    parts = []
    direct = df[df["kind"] == KIND_SNOW24].copy()
    if len(direct):
        direct["snow24_in"] = direct["value_in"]
        parts.append(direct)
    for _, grp in df[df["kind"] == KIND_SEASON_TOTAL].groupby("station_id"):
        grp = grp.sort_values("date").reset_index(drop=True)
        prev_day_gap = pd.to_datetime(grp["date"]).diff().dt.days
        grp["snow24_in"] = (grp["value_in"].diff().clip(lower=0)).round(2)
        grp = grp[prev_day_gap == 1]
        parts.append(grp)

    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=RAW_COLUMNS + ["date", "snow24_in"])
    out = out[(out["date"] >= begin) & (out["date"] <= end)]
    out = out.dropna(subset=["snow24_in"])
    return out[["station_id", "date", "snow24_in", "scraped_utc"]].sort_values(
        ["station_id", "date"]
    ).reset_index(drop=True)

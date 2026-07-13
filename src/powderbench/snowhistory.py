"""Southern-hemisphere snow-station archive: public station series kept in
the repo and served to the site's season explorer.

Unlike leagues (scored competitions), this is a curated read-only archive of
every genuinely public SH snow station we can find: INA snow-level telemetry
(Argentina), Snowy Hydro snow courses back to 1954 (Australia), the Victorian
government's CC-BY resort depth records, and whatever else the census turns
up. data/snowhistory/catalog.yaml lists networks (with licences) and
stations; data/snowhistory/<network>/<slug>.csv holds each tidy series
(date,<value column>).

Depth/level series become cumulative "fresh snow" curves the same way SNOTEL
truth does: positive deltas between consecutive readings (lumped onto the
later reading when the cadence is weekly), clamped at zero, summed through
the austral season. Settling makes this a floor, not a gauge — fine for the
explorer's year-vs-year shapes.
"""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import yaml

from .stations import data_dir

CM_PER_IN = 2.54
SEASON_START_MONTH = 4  # austral snow year
MAX_DELTA_GAP_DAYS = 21  # longer gaps reset the baseline instead of lumping


def history_dir() -> Path:
    return data_dir() / "snowhistory"


def load_catalog() -> dict:
    path = history_dir() / "catalog.yaml"
    if not path.exists():
        return {"networks": {}, "stations": []}
    return yaml.safe_load(path.read_text())


def _read_series(network: str, slug: str, value_scale_in: float) -> list[tuple[date, float]]:
    """Read a station CSV, scaling the raw value into inches (of snow, or of
    water for SWE — both plotted in inches so leagues and archive share a y-axis)."""
    path = history_dir() / network / f"{slug}.csv"
    rows = []
    with path.open() as f:
        reader = csv.reader(f)
        next(reader)  # header: date,<value>
        for row in reader:
            if len(row) < 2 or not row[1]:
                continue
            rows.append((date.fromisoformat(row[0]), float(row[1]) * value_scale_in))
    rows.sort()
    return rows


def _season_levels(rows: list[tuple[date, float]]) -> dict[str, list]:
    """Per-season daily arrays of the value itself (forward-filled) — used for
    SWE snow-pillow series, where the seasonal snowpack curve is the story,
    not a fresh-snow reconstruction. Values already in inches (of water).

    (Reads inches directly since _read_series has already scaled.)"""
    ssm = SEASON_START_MONTH
    marks: dict[int, dict[int, float]] = {}
    for d, value_in in rows:
        season = d.year if d.month >= ssm else d.year - 1
        idx = (d - date(season, ssm, 1)).days
        if 0 <= idx < 365:
            marks.setdefault(season, {})[idx] = round(max(value_in, 0.0), 1)
    out = {}
    for season, by_idx in sorted(marks.items()):
        first, last = min(by_idx), max(by_idx)
        arr, current = [], None
        for i in range(365):
            if i < first or i > last:
                arr.append(None)
                continue
            if i in by_idx:
                current = by_idx[i]
            arr.append(current)
        out[str(season)] = arr
    return out


def _season_cumulatives(rows: list[tuple[date, float]]) -> dict[str, list]:
    """Per-season daily arrays of cumulative fresh snow (inches): None before
    the first reading, forward-filled between readings, None after the last.
    Values already in inches (of snow); accumulates positive depth deltas."""
    ssm = SEASON_START_MONTH
    marks: dict[int, dict[int, float]] = {}
    running: dict[int, float] = {}
    prev: tuple[date, float] | None = None
    for d, value_in in rows:
        season = d.year if d.month >= ssm else d.year - 1
        idx = (d - date(season, ssm, 1)).days
        if not 0 <= idx < 365:
            prev = (d, value_in)
            continue
        delta = 0.0
        if prev is not None:
            pd_, pv = prev
            p_season = pd_.year if pd_.month >= ssm else pd_.year - 1
            if p_season == season and (d - pd_).days <= MAX_DELTA_GAP_DAYS:
                delta = max(value_in - pv, 0.0)
        running[season] = running.get(season, 0.0) + delta
        marks.setdefault(season, {})[idx] = round(running[season], 1)
        prev = (d, value_in)

    out = {}
    for season, by_idx in sorted(marks.items()):
        first, last = min(by_idx), max(by_idx)
        arr, current = [], None
        for i in range(365):
            if i < first or i > last:
                arr.append(None)
                continue
            if i in by_idx:
                current = by_idx[i]
            arr.append(current)
        out[str(season)] = arr
    return out


def build_snowhistory_site() -> list[Path]:
    """Export the archive to site/data/seasons/southern-stations/ in the same
    shape the season explorer already reads for leagues."""
    catalog = load_catalog()
    if not catalog["stations"]:
        return []
    out_dir = data_dir().parent / "site" / "data" / "seasons" / "southern-stations"
    out_dir.mkdir(parents=True, exist_ok=True)

    # raw unit -> inches; SWE (water mm/cm) and depth/level (snow) share the axis
    SCALE_IN = {"level_m": 100 / CM_PER_IN, "depth_cm": 1 / CM_PER_IN,
                "swe_mm": 0.1 / CM_PER_IN, "swe_cm": 1 / CM_PER_IN}

    written, index = [], []
    for st in catalog["stations"]:
        network = catalog["networks"][st["network"]]
        variable = st.get("variable") or network["variable"]  # per-station override
        if variable not in SCALE_IN:
            continue
        rows = _read_series(st["network"], st["slug"], SCALE_IN[variable])
        is_swe = variable.startswith("swe")
        seasons = _season_levels(rows) if is_swe else _season_cumulatives(rows)
        if not seasons:
            continue
        metric = "peak SWE" if is_swe else "cumulative snow"
        label = f"{st['name']} · {network['short']}"
        payload = {
            "station_id": st["slug"],
            "resort": label,
            "season_start_month": SEASON_START_MONTH,
            "metric": metric,
            "seasons": seasons,
        }
        path = out_dir / f"{st['slug']}.json"
        path.write_text(json.dumps(payload))
        written.append(path)
        latest_total = max((v for v in seasons[max(seasons)] if v is not None), default=0)
        index.append(
            {"slug": st["slug"], "station_id": st["slug"], "resort": label,
             "metric": metric, "seasons": sorted(seasons), "latest_total": latest_total}
        )

    index.sort(key=lambda e: -len(e["seasons"]))  # deepest history first
    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps({"season_start_month": SEASON_START_MONTH, "stations": index}, indent=1))
    written.append(index_path)
    return written

"""Truth adapters: one QC'd daily-snowfall frame schema, multiple sources.

Every adapter returns rows of (station_id, date, snow24, valid, qc_flag), so
window_truth, scoring, rounds, climatology, and hindcast are source-agnostic.

- snotel: snow-depth deltas cross-checked against SWE (see truth.py) — real
  ground stations, used by the stations league.
- era5: ERA5 reanalysis daily snowfall via Open-Meteo's keyless archive API —
  model-analysis truth for the era5 league (no public SH station
  API exists; see docs/DATA.md). QC is missing-data only.
- resort: each resort's own published snow report, archived twice daily by
  the scrape cron (resortfeeds) — truth for the resorts league. Truth reads
  the committed archive, never the live site; a missed scrape voids that
  station-day for everyone.

A station's `truth_source` field can override its league default, which is how
real feeds (NIWA, DGA) get promoted per-station once they prove stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from . import snotel, truth
from .leagues import League
from .openmeteo import era5_daily_snowfall
from .stations import load_stations

QC_OK = truth.QC_OK
QC_MISSING = truth.QC_MISSING


def _era5_daily(station_ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    wanted = set(station_ids)
    stations = [s for s in load_stations(league.name) if s.station_id in wanted]
    raw = era5_daily_snowfall(stations, begin, end)
    raw = raw.rename(columns={"snowfall_in": "snow24"})
    raw["valid"] = raw["snow24"].notna()
    raw["qc_flag"] = raw["valid"].map({True: QC_OK, False: QC_MISSING})
    return raw[["station_id", "date", "snow24", "valid", "qc_flag"]]


def _snotel_daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    # depth deltas need the prior day's reading
    obs = snotel.fetch_daily(station_ids, begin - timedelta(days=1), end)
    daily = truth.daily_snowfall(obs)
    return daily[(daily["date"] >= begin) & (daily["date"] <= end)].reset_index(drop=True)


def _resort_daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """Resorts-league truth from the committed scrape archive, reindexed to
    the full station x date grid: no archived report -> voided (missing);
    beyond-world-record values -> voided (implausible)."""
    from .resortfeeds import daily_from_archive

    obs = daily_from_archive(begin, end, station_ids)
    have = {(r.station_id, r.date): float(r.snow24_in) for r in obs.itertuples()}
    rows = []
    for sid in station_ids:
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            snow = have.get((sid, d))
            flag = QC_OK
            if snow is None:
                flag = QC_MISSING
            elif snow > truth.MAX_PLAUSIBLE_SNOW24_IN:
                flag = truth.QC_IMPLAUSIBLE
            valid = flag == QC_OK
            rows.append(
                {
                    "station_id": sid,
                    "date": d,
                    "snow24": snow if valid else float("nan"),
                    "valid": valid,
                    "qc_flag": flag,
                }
            )
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])


@dataclass(frozen=True)
class TruthSource:
    """One ground-truth network. `fetch(ids, league, begin, end)` returns the
    shared (station_id, date, snow24, valid, qc_flag) frame.

    history_begin: where the site's season explorer starts reading.
    climatology: default (begin, end) for build-climatology, and the source the
        climatology is built from (None = itself; resorts have no report
        history, so theirs comes from ERA5 at the same coordinates).
    measured: True when the network records new snow directly (an observer's
        board, a manual HN); False when snow24 is derived from depth deltas.
    """

    name: str
    fetch: Callable[[list[str], League, date, date], pd.DataFrame]
    history_begin: date | None
    climatology: tuple[date, date]
    measured: bool
    note: str
    climatology_source: str | None = None


TRUTH_SOURCES: dict[str, TruthSource] = {}


def register(source: TruthSource) -> TruthSource:
    TRUTH_SOURCES[source.name] = source
    return source


def get_source(name: str) -> TruthSource:
    try:
        return TRUTH_SOURCES[name]
    except KeyError:
        raise ValueError(f"unknown truth source: {name!r} (have {sorted(TRUTH_SOURCES)})") from None


_CLIM_END = date(2025, 6, 30)

register(TruthSource(
    name="snotel",
    fetch=lambda ids, league, b, e: _snotel_daily(ids, b, e),
    history_begin=date(2015, 10, 1),
    climatology=(date(2015, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "Ground truth comes from NRCS SNOTEL telemetry — automated snow pillows and depth "
        "sensors high in the mountains, QC’d and voided (for everyone) when sensors glitch. "
        "Hover a marker for the station behind each zone."
    ),
))
register(TruthSource(
    name="era5",
    fetch=_era5_daily,
    history_begin=date(1991, 1, 1),
    climatology=(date(1991, 1, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is ERA5 model analysis at each resort’s coordinates (no public "
        "southern-hemisphere station API exists — real feeds get promoted as they become "
        "available). Scores resolve about a week after each round."
    ),
))
register(TruthSource(
    name="resort",
    fetch=lambda ids, league, b, e: _resort_daily(ids, b, e),
    history_begin=None,  # the scrape archive is the history
    climatology=(date(1991, 1, 1), _CLIM_END),
    climatology_source="era5",
    measured=True,
    note=(
        "Truth is each resort’s own published snow report, archived twice daily from the "
        "resort’s site. Resort numbers run generous — but every team is scored against the "
        "same report, so it’s a fair fight; cross-check the ERA5 league for the reanalysis view."
    ),
))


def _offsets(league: League, ids: list[str]) -> dict[str, int]:
    wanted = set(ids)
    return {s.station_id: s.day_offset for s in load_stations(league.name)
            if s.station_id in wanted and s.day_offset is not None}


def _ghcnd_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import ghcnd

    return ghcnd.daily(ids, begin, end, _offsets(league, ids))


def _eccc_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import eccc

    return eccc.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="ghcnd",
    fetch=_ghcnd_daily,
    history_begin=date(2016, 10, 1),
    climatology=(date(2016, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is measured new snow from volunteer and NWS observers (CoCoRaHS + NWS COOP, "
        "via NOAA GHCN-Daily): a person, a snowboard and a ruler every morning. A day's "
        "snow is the next morning's report. Scores resolve ~6 days after each round, once "
        "late reports are in."
    ),
))


register(TruthSource(
    name="eccc",
    fetch=_eccc_daily,
    history_begin=date(2016, 10, 1),
    climatology=(date(2016, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is measured new snow from Environment and Climate Change Canada's staffed "
        "climate stations (TOTAL_SNOW), the Canadian counterpart of the US observer network."
    ),
))


def _slf_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import slf

    return slf.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="slf",
    fetch=_slf_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the 24h new snow (HN) measured by the WSL Institute for Snow and Avalanche "
        "Research SLF — observers' snowboards at manual study plots across the Swiss Alps "
        "(Zermatt, Verbier, St. Moritz, Davos…) plus SLF's automatic IMIS stations, and hand "
        "measurements at GeoSphere Austria's climate stations (St. Anton, Obergurgl, Saalbach…). "
        "Data © SLF and © GeoSphere Austria, CC BY 4.0."
    ),
))


def _geosphere_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import geosphere

    return geosphere.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="geosphere",
    fetch=_geosphere_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the 24h new snow measured by hand at GeoSphere Austria's climate stations "
        "(St. Anton, Obergurgl, Obertauern, Saalbach…), read at the 07:00 morning observation. "
        "Data © GeoSphere Austria, CC BY 4.0."
    ),
))


def _snolite_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import snolite

    return snolite.daily(ids, begin, end)


register(TruthSource(
    name="snolite",
    fetch=_snolite_daily,
    history_begin=date(2015, 10, 1),
    climatology=(date(2015, 10, 1), _CLIM_END),
    measured=False,
    note="NRCS SNOLITE depth-only telemetry; big jumps confirmed by neighbouring SNOTEL SWE.",
))


def _cdec_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import cdec

    return cdec.daily(ids, begin, end)


register(TruthSource(
    name="cdec",
    fetch=_cdec_daily,
    history_begin=date(2021, 10, 1),
    climatology=(date(2021, 10, 1), _CLIM_END),
    measured=False,
    note="California DWR CDEC snow pillows: hourly depth at local midnight, SNOTEL-style QC.",
))


def daily_truth(league: League, station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    """QC'd daily snowfall for [begin, end], dispatched by truth source.
    Stations with a per-station override are fetched from their own source."""
    wanted = set(station_ids)
    groups: dict[str, list[str]] = {}
    for s in load_stations(league.name):
        if s.station_id in wanted:
            groups.setdefault(s.truth_source or league.truth_source, []).append(s.station_id)

    frames = [get_source(source).fetch(ids, league, begin, end) for source, ids in groups.items()]
    if not frames:
        return pd.DataFrame(columns=["station_id", "date", "snow24", "valid", "qc_flag"])
    return pd.concat(frames, ignore_index=True)

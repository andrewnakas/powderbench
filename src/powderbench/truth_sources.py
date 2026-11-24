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
        "sensors high in the mountains, QC’d and voided (for everyone) when sensors glitch."
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


def _nivo_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import nivo

    return nivo.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="nivo",
    fetch=_nivo_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the 24h new snow measured on the snow board by observers at French ski "
        "resorts (Météo-France nivo-meteorological network: Val d'Isère, Tignes, Chamonix, "
        "Alpe d'Huez…). Data © Météo-France, Licence Ouverte 2.0."
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


def _jma_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import jma

    wanted = set(ids)
    ext = {s.station_id: s.external_id for s in load_stations(league.name)
           if s.station_id in wanted and s.external_id}
    return jma.daily({sid: ext[sid] for sid in ids if sid in ext}, begin, end, _offsets(league, ids))


register(TruthSource(
    name="jma",
    fetch=_jma_daily,
    history_begin=date(2015, 10, 1),
    # one archive page per station-month: ten winters keep the build to ~10k pages
    climatology=(date(2015, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is the daily snowfall total (降雪の深さの合計) from the Japan Meteorological "
        "Agency's automatic snow-depth sensors at AMeDAS stations (Sukayu/Hakkoda, Kutchan/Niseko, "
        "Hakuba, Nozawa, Myoko…): the sum of each hour's rise in snow depth over the JST day. "
        "Source: 気象庁ホームページ (Japan Meteorological Agency)."
    ),
))


def _smhi_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import smhi

    return smhi.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="smhi",
    fetch=_smhi_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2010, 10, 1), _CLIM_END),
    measured=False,
    note=(
        # the nordic league's note on the site, so it credits all three networks
        "Truth is new snow from the daily morning snow-depth reading at Swedish (SMHI), Finnish (FMI) "
        "and Norwegian (MET Norway) weather stations (Sälen, Storlien, Saariselkä, Salla, Hemsedal, "
        "Oppdal…): the rise in depth between two morning readings. Data © SMHI, © Finnish "
        "Meteorological Institute and MET Norway (frost.met.no), CC BY 4.0."
    ),
))


def _fmi_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import fmi

    return fmi.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="fmi",
    fetch=_fmi_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2010, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is new snow from the morning snow-depth reading at the Finnish Meteorological "
        "Institute's weather stations (Saariselkä, Paljakka, Salla, Kittilä Kenttärova by Levi…): the rise in "
        "depth to the next morning's reading. Data © Finnish Meteorological Institute, CC BY 4.0."
    ),
))


def _frost_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import frost

    return frost.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="frost",
    fetch=_frost_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2010, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is new snow from the daily 07:00 snow-depth reading at MET Norway's weather "
        "stations (Hemsedal, Trysil, Geilo, Oppdal…): the rise in depth to the next morning's reading, "
        "voided when the station's own rain gauge stayed dry (wind drift). Data © MET Norway (Frost), "
        "CC BY 4.0."
    ),
))


def _dwd_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import dwd

    return dwd.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="dwd",
    fetch=_dwd_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the new snow measured by hand at the morning reading of Deutscher Wetterdienst "
        "stations in the Bavarian Alps (Garmisch/Eibsee, Oberstdorf, Balderschwang, Berchtesgaden, "
        "Reit im Winkl…). Data © Deutscher Wetterdienst, CC BY 4.0."
    ),
))


def _meteotn_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import meteotn

    wanted = set(ids)
    ext = {s.station_id: s.external_id for s in load_stations(league.name) if s.station_id in wanted and s.external_id}
    return meteotn.daily({sid: ext[sid] for sid in ids if sid in ext}, begin, end, _offsets(league, ids))


register(TruthSource(
    name="meteotn",
    fetch=_meteotn_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2005, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the new snow measured on a snow board at the 08:00 reading of Meteotrentino's "
        "snow fields in the Italian Dolomites and Brenta (Passo Tonale, Presena, Grostè by Madonna di "
        "Campiglio, Folgarida, Canazei, Passo San Pellegrino…). Data © Provincia Autonoma di Trento "
        "— Meteotrentino, CC BY 4.0."
    ),
))


def _lwdtirol_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import lwdtirol

    return lwdtirol.daily(ids, begin, end)


register(TruthSource(
    name="lwdtirol",
    fetch=_lwdtirol_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is new snow from the automatic snow-depth sensors of Land Tirol's avalanche warning and "
        "hydrographic services, many of them on ski areas (Sölden, Pitztal glacier, Ischgl, Obergurgl, "
        "Kühtai…): the rise in depth from one local midnight to the next. Data © Land Tirol "
        "(data.tirol.gv.at), CC BY 4.0."
    ),
))


def _provbz_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import provbz

    return provbz.daily(ids, begin, end)


register(TruthSource(
    name="provbz",
    fetch=_provbz_daily,
    history_begin=date(2015, 10, 1),
    climatology=(date(2014, 9, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is new snow from the automatic snow-depth sensors of the South Tyrol provincial weather "
        "service, many on Dolomites ski areas (Alta Badia, Obereggen, Sulden, Karerpass, Sexten…): the "
        "rise in depth from one local midnight to the next. Data: Provincia autonoma di Bolzano – "
        "Alto Adige (data.civis.bz.it), CC0 1.0."
    ),
))

def _arpal_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import arpal

    return arpal.daily(ids, begin, end)


register(TruthSource(
    name="arpal",
    fetch=_arpal_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "Truth is new snow from ARPA Lombardia's automatic snow-depth sensors, several on ski areas "
        "(Livigno, Bormio, Aprica, Madesimo, Valmalenco…): the rise in depth from one local midnight "
        "to the next, counted from 2 cm. Data: ARPA Lombardia (dati.lombardia.it), CC0 1.0."
    ),
))

def _meteoswiss_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import meteoswiss

    return meteoswiss.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="meteoswiss",
    fetch=_meteoswiss_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the new snow measured by hand at 06 UTC at MeteoSwiss's manual stations in the ski "
        "villages and valleys below the SLF study plots (Engelberg, Grindelwald, Pontresina, Sedrun, "
        "Crans-Montana, Säntis…). Source: MeteoSwiss (opendata.meteoswiss.ch), CC BY 4.0."
    ),
))


def _chmi_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import chmi

    return chmi.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="chmi",
    fetch=_chmi_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=True,
    note=(
        "Truth is the new snow measured by hand at the morning reading of Czech Hydrometeorological "
        "Institute stations in the Krkonoše, Jeseníky, Šumava and Krušné hory (Luční bouda, Labská bouda, "
        "Pec pod Sněžkou, Praděd, Churáňov, Klínovec…). Data © ČHMÚ (opendata.chmi.cz), CC BY 4.0."
    ),
))


def _meteofrance_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import meteofrance

    return meteofrance.daily(ids, begin, end, _offsets(league, ids))


register(TruthSource(
    name="meteofrance",
    fetch=_meteofrance_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2010, 10, 1), _CLIM_END),
    measured=False,  # most stations are depth-derived; the resort posts' boards are measured
    note=(
        "Truth is the 24 h new snow on Météo-France ski-resort snow boards (HNEIGEF), or the rise in the "
        "06 UTC snow depth (NEIGETOT06) at Météo-France weather stations in the Pyrenees, Massif Central, "
        "Vosges, Jura and Corsica. Data: Météo-France, Licence Ouverte 2.0."
    ),
))


def _ghcnh_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import ghcnh

    wanted = set(ids)
    lons = {s.station_id: s.longitude for s in load_stations(league.name) if s.station_id in wanted}
    return ghcnh.daily(ids, begin, end, lons)


register(TruthSource(
    name="ghcnh",
    fetch=_ghcnh_daily,
    history_begin=date(2010, 10, 1),
    climatology=(date(2000, 10, 1), _CLIM_END),
    measured=False,
    note=(
        # the world league's note on the site, so it credits every network in the league
        "Truth is the morning snow reading at mountain weather stations: new snow measured on a board where "
        "the station has one (DWD observers in the Black Forest, Bavarian Forest, Fichtelgebirge, Erzgebirge "
        "and Harz; ČHMÚ in the Krkonoše, Jizera and Krušné hory; Météo-France ski-resort posts in the "
        "Pyrenees), otherwise the rise in snow depth to the next morning's reading (WMO stations via NOAA "
        "GHCNh in the Low Tatras and Palandöken; Météo-France stations in the Pyrenees, Massif Central, "
        "Vosges, Jura and Corsica). Data: NOAA NCEI GHCNh (CC0 1.0), Deutscher Wetterdienst (CC BY 4.0), "
        "© ČHMÚ (CC BY 4.0), Météo-France (Licence Ouverte 2.0)."
    ),
))


def _bcasws_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import bcasws

    return bcasws.daily(ids, begin, end)


register(TruthSource(
    name="bcasws",
    fetch=_bcasws_daily,
    history_begin=date(2015, 10, 1),
    climatology=(date(2015, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "British Columbia automated snow pillows from the province's own feed: hourly depth at "
        "local midnight, SNOTEL-style QC. Contains information licensed under the Open Government "
        "Licence – British Columbia."
    ),
))


def _bcasws_depth_daily(ids: list[str], league: League, begin: date, end: date) -> pd.DataFrame:
    from .networks import bcasws

    return bcasws.daily_depth_only(ids, begin, end)


register(TruthSource(
    name="bcasws_depth",
    fetch=_bcasws_depth_daily,
    history_begin=date(2015, 10, 1),
    climatology=(date(2015, 10, 1), _CLIM_END),
    measured=False,
    note=(
        "British Columbia depth-only snow stations (no pillow) from the province's own feed: hourly "
        "depth at local midnight; big jumps confirmed by an SWE rise at a pillow within 40 km, as for "
        "SNOLITE. Contains information licensed under the Open Government Licence – British Columbia."
    ),
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

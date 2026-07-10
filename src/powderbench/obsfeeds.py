"""Observation feeds: best-effort real-world snow observations recorded
alongside model truth in the southern league.

A feed is a fetcher returning rows of (station_id, date, snow24_obs_in). Feeds
never block round resolution — they're reference data, stored under
data/obs/<league>/ and surfaced next to truth in round results. Once a feed
proves stable for a station, that station's `truth_source` in stations.yaml
can be flipped to promote it to actual truth (see truth_sources.py).

Status of known southern sources (recon 2026-07, see docs/DATA.md):
- Argentina INA a5 (alerta.ina.gob.ar/a5): OPEN JSON API, 47 real "nivel de
  nieve" telemetry stations across the Andes — incl. NIV Las Leñas ~1 km from
  our las-lenas point and NIV Túnel Internacional near Portillo. Public data
  currently ends mid-2024 (2022-24 usable for validating ERA5); the feed is
  implemented and will light up automatically if their sync resumes.
- Snowy Hydro (AU): daily snow depth at Spencers Creek (site 00003, 1830 m,
  Perisher/Thredbo massif) published as a daily-regenerated HYPLOT chart PDF.
  Values are recovered from the chart's vector path — approximate (±2 cm)
  and fragile by nature, fine for reference columns.
- Chile DGA: real hourly nivometric telemetry exists, but every public front
  (JSP portals, Shiny app, Angular observatorio) hides the endpoint behind
  app/session auth. Slot ships disabled.
- NIWA (NZ) Snow & Ice Network: real stations behind the DataHub API. The
  adapter activates when NIWA_API_KEY and NIWA_CUSTOMER_ID are set.
- Resort snow reports: aggregators prohibit scraping and resort marketing
  numbers are inflated/gameable — not used.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from .stations import data_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Feed:
    name: str
    league: str
    enabled: Callable[[], bool]
    fetch: Callable[[date, date], pd.DataFrame]  # -> station_id, date, snow24_obs_in
    # False = licence forbids republishing raw values (e.g. NIWA Non-Commercial
    # Use Licence cl. 2a): store locally under data/obs/<league>/private/
    # (gitignored) and keep out of committed round results entirely.
    publish_raw: bool = True


def _niwa_enabled() -> bool:
    return bool(os.environ.get("NIWA_API_KEY") and os.environ.get("NIWA_CUSTOMER_ID"))


def _niwa_fetch(begin: date, end: date) -> pd.DataFrame:
    """Skeleton: wire up once the user's DataHub credentials exist. NIWA SIN
    station mapping (station_id here -> NIWA site number) lives in the feed
    config when activated."""
    raise NotImplementedError(
        "NIWA feed: credentials detected but the DataHub endpoint mapping is "
        "not configured yet — see docs/DATA.md#observation-feeds"
    )


def _dga_enabled() -> bool:
    return False  # real telemetry exists but all public fronts are app/session-gated


def _dga_fetch(begin: date, end: date) -> pd.DataFrame:
    raise NotImplementedError("DGA feed disabled: no stable public endpoint")


# INA a5: series_id -> our station_id. NIV stations chosen for proximity to
# registry points (Las Leñas ~1 km; Túnel Internacional ~7 km from Portillo,
# same pass; Tronador ~20 km from Catedral; Martial ~20 km from Castor).
INA_BASE = "https://alerta.ina.gob.ar/a5"
INA_SERIES = {
    33468: "las-lenas:AR:ERA5",       # NIV Aº de Las Leñas
    32980: "portillo:CL:ERA5",        # NIV Túnel Internacional (Cristo Redentor pass)
    33283: "catedral:AR:ERA5",        # NIV C. Tronador - Otto Meiling
    44006: "castor:AR:ERA5",          # NIV Glaciar Martial (Ushuaia)
}


def _ina_enabled() -> bool:
    return True


def _ina_fetch(begin: date, end: date) -> pd.DataFrame:
    """Daily fresh snow from INA snow-level (m) telemetry: positive day-over-day
    delta of the daily-max level, converted to inches. Empty when the public
    sync has no data for the window (currently: anything after mid-2024)."""
    import requests

    rows = []
    for series_id, station_id in INA_SERIES.items():
        resp = requests.get(
            f"{INA_BASE}/obs/puntual/series/{series_id}/observaciones",
            params={
                "timestart": (begin - timedelta(days=1)).isoformat(),
                "timeend": (end + timedelta(days=1)).isoformat(),
                "limit": 2000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        obs = payload if isinstance(payload, list) else payload.get("rows", [])
        if not obs:
            continue
        df = pd.DataFrame(
            {"ts": pd.to_datetime(o["timestart"]), "level_m": o.get("valor")} for o in obs
        ).dropna()
        daily_max = df.set_index("ts")["level_m"].resample("D").max()
        delta_in = (daily_max.diff().clip(lower=0) * 39.3701).round(2)
        for ts, snow in delta_in.items():
            d = ts.date()
            if begin <= d <= end and pd.notna(snow):
                rows.append({"station_id": station_id, "date": d, "snow24_obs_in": float(snow)})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24_obs_in"])


SNOWY_PDF = "https://www.snowyhydro.com.au/wp-content/uploads/pdfs/watrel/00003SD.pdf"


def _snowy_enabled() -> bool:
    return True


def _snowy_fetch(begin: date, end: date) -> pd.DataFrame:
    """Spencers Creek daily snow depth, recovered from Snowy Hydro's
    daily-regenerated HYPLOT chart PDF (May-Nov window, 0-300 cm axis).
    Mapped to the Perisher registry point as reference data."""
    from .snowy_pdf import extract_daily_depths

    depths = extract_daily_depths(SNOWY_PDF)  # date -> depth_cm
    if not depths:
        return pd.DataFrame(columns=["station_id", "date", "snow24_obs_in"])
    series = pd.Series(depths).sort_index()
    delta_in = (series.diff().clip(lower=0) / 2.54).round(2)
    rows = [
        {"station_id": "perisher:AU:ERA5", "date": d, "snow24_obs_in": float(v)}
        for d, v in delta_in.items()
        if begin <= d <= end and pd.notna(v)
    ]
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24_obs_in"])


FEEDS: tuple[Feed, ...] = (
    Feed("ina", "southern", _ina_enabled, _ina_fetch),
    Feed("snowyhydro", "southern", _snowy_enabled, _snowy_fetch),
    Feed("niwa", "southern", _niwa_enabled, _niwa_fetch, publish_raw=False),
    Feed("dga", "southern", _dga_enabled, _dga_fetch),
)


def collect_observations(league_name: str, begin: date, end: date) -> pd.DataFrame:
    """Run every enabled feed for a league; persist and return the publishable
    part. Failures are logged, never raised — feeds must not block resolution.

    Feeds with publish_raw=False are written only to data/obs/<league>/private/
    (gitignored) and excluded from the returned frame, so their raw values
    never reach committed round results or the site.
    """
    public, private = [], []
    for feed in FEEDS:
        if feed.league != league_name or not feed.enabled():
            continue
        try:
            df = feed.fetch(begin, end)
            df["feed"] = feed.name
            (public if feed.publish_raw else private).append(df)
        except Exception:
            log.warning("obs feed %s failed; continuing without it", feed.name, exc_info=True)

    obs_dir = data_dir() / "obs" / league_name
    stem = f"{begin.isoformat()}_{end.isoformat()}.csv"
    if private:
        priv_dir = obs_dir / "private"
        priv_dir.mkdir(parents=True, exist_ok=True)
        pd.concat(private, ignore_index=True).to_csv(priv_dir / stem, index=False)
    if not public:
        return pd.DataFrame(columns=["station_id", "date", "snow24_obs_in", "feed"])
    out = pd.concat(public, ignore_index=True)
    obs_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(obs_dir / stem, index=False)
    return out

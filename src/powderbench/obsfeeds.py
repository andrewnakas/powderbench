"""Observation feeds: best-effort real-world snow observations recorded
alongside model truth in the southern league.

A feed is a fetcher returning rows of (station_id, date, snow24_obs_in). Feeds
never block round resolution — they're reference data, stored under
data/obs/<league>/ and surfaced next to truth in round results. Once a feed
proves stable for a station, that station's `truth_source` in stations.yaml
can be flipped to promote it to actual truth (see truth_sources.py).

Status of known southern sources (probed 2026-07):
- Chile DGA / Observatorio Andino: real stations exist, but access is JSP
  portals and an R Shiny websocket app — no stable REST endpoint found. The
  DGA feed below ships disabled until a stable endpoint is identified.
- NIWA (NZ) Snow & Ice Network: real stations behind the DataHub API
  (customer id + key). The adapter activates when NIWA_API_KEY and
  NIWA_CUSTOMER_ID are set.
- Resort snow reports: aggregators prohibit scraping and resort marketing
  numbers are inflated/gameable — not used. Individual resorts with
  legitimately public data can be added as feeds case-by-case.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
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
    return False  # no stable endpoint found (Shiny/JSP portals only)


def _dga_fetch(begin: date, end: date) -> pd.DataFrame:
    raise NotImplementedError("DGA feed disabled: no stable public endpoint")


FEEDS: tuple[Feed, ...] = (
    Feed("niwa", "southern", _niwa_enabled, _niwa_fetch),
    Feed("dga", "southern", _dga_enabled, _dga_fetch),
)


def collect_observations(league_name: str, begin: date, end: date) -> pd.DataFrame:
    """Run every enabled feed for a league; persist and return what came back.
    Failures are logged, never raised — feeds must not block resolution."""
    frames = []
    for feed in FEEDS:
        if feed.league != league_name or not feed.enabled():
            continue
        try:
            df = feed.fetch(begin, end)
            df["feed"] = feed.name
            frames.append(df)
        except Exception:
            log.warning("obs feed %s failed; continuing without it", feed.name, exc_info=True)
    if not frames:
        return pd.DataFrame(columns=["station_id", "date", "snow24_obs_in", "feed"])
    out = pd.concat(frames, ignore_index=True)
    obs_dir = data_dir() / "obs" / league_name
    obs_dir.mkdir(parents=True, exist_ok=True)
    path = obs_dir / f"{begin.isoformat()}_{end.isoformat()}.csv"
    out.to_csv(path, index=False)
    return out

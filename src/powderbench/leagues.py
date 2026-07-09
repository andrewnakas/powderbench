"""League registry: each league has its own stations, truth source, cutoff and
maturity rules, and result paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import yaml

from .stations import data_dir

DEFAULT_LEAGUE = "northern"


@dataclass(frozen=True)
class League:
    name: str
    label: str
    status: str  # live | trial
    truth_source: str  # snotel | era5
    cutoff_days_before: int
    cutoff_hour_utc: int
    resolve_after_days: int
    resolve_after_hour_utc: int
    season_start_month: int

    def cutoff_utc(self, round_date: date) -> datetime:
        d = round_date - timedelta(days=self.cutoff_days_before)
        return datetime(d.year, d.month, d.day, self.cutoff_hour_utc, tzinfo=timezone.utc)

    def matured_at_utc(self, round_date: date) -> datetime:
        d = round_date + timedelta(days=self.resolve_after_days)
        return datetime(d.year, d.month, d.day, self.resolve_after_hour_utc, tzinfo=timezone.utc)

    def matured(self, round_date: date, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.matured_at_utc(round_date)

    @property
    def climatology_path(self) -> str:
        return f"climatology/{self.name}.csv"


@lru_cache(maxsize=1)
def load_leagues() -> tuple[League, ...]:
    raw = yaml.safe_load((data_dir() / "leagues.yaml").read_text())
    return tuple(
        League(
            name=entry["name"],
            label=entry["label"],
            status=entry["status"],
            truth_source=entry["truth_source"],
            cutoff_days_before=entry["cutoff"]["days_before"],
            cutoff_hour_utc=entry["cutoff"]["hour_utc"],
            resolve_after_days=entry["resolve_after"]["days"],
            resolve_after_hour_utc=entry["resolve_after"]["hour_utc"],
            season_start_month=entry["season_start_month"],
        )
        for entry in raw["leagues"]
    )


def get_league(name: str) -> League:
    for league in load_leagues():
        if league.name == name:
            return league
    raise KeyError(f"unknown league: {name!r} (have {[l.name for l in load_leagues()]})")

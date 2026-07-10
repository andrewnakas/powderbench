"""Resort snow-report specs: one declarative entry per scraped resort site."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

CM_PER_IN = 2.54


@dataclass(frozen=True)
class ResortSpec:
    resort_id: str  # slug, matching the era5 point slug ("cardrona")
    station_id: str  # "cardrona:NZ:RESORT" — key into stations.yaml
    country: str  # NZ | AU | CL | AR
    tz: str  # IANA zone the resort reports in
    url: str  # page or JSON endpoint fetched each run
    parse: Callable[[str], float | None]  # body -> value in `unit`; None if absent
    unit: str = "cm"  # "cm" | "in" — converted centrally to inches
    # False: parse returns the 24h snowfall directly. True: parse returns the
    # season-total-to-date (some sites publish only that); daily snowfall is
    # derived read-side from day-over-day deltas, like the Snowy Hydro feed.
    cumulative: bool = False
    report_hour_local: int = 6  # hour the morning report refreshes
    enabled: bool = False
    # Scrape-consent audit stamp: date + robots.txt/ToS outcome + endpoint
    # provenance. The registry test enforces enabled => verified.
    verified: str | None = None
    headers: Mapping[str, str] | None = None
    notes: str = ""

    def to_inches(self, value: float) -> float:
        """Reported value -> inches, clamped to >= 0."""
        inches = value / CM_PER_IN if self.unit == "cm" else value
        return round(max(inches, 0.0), 2)

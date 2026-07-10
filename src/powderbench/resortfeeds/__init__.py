"""Scrapers for individual ski-resort snow reports (resorts-league truth).

Aggregators (OnTheSnow, Snow-Forecast, ...) are off-limits — their ToS
prohibit scraping. Every entry in the registry is an individual resort site
vetted case-by-case; a spec is only enabled once its robots.txt/ToS review is
recorded in its `verified` stamp. See docs/RESORTS.md for the onboarding
checklist.
"""

from .archive import daily_from_archive, scrape_all
from .registry import REGISTRY
from .spec import ResortSpec

__all__ = ["REGISTRY", "ResortSpec", "daily_from_archive", "scrape_all"]

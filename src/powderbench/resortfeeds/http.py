"""Polite HTTP for resort scraping: identify ourselves, honor robots.txt,
space out requests, back off on errors."""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "PowderBench/0.1 (+https://powderbench.com; daily snow-report archive; "
    "github.com/andrewnakas/powderbench)"
)
TIMEOUT = 30
RETRIES = 3
HOST_SPACING_S = 3.0

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}


def robots_allowed(url: str, ua: str = USER_AGENT) -> bool:
    """True if robots.txt permits fetching url. A parseable disallow is always
    honored. Fetched with our own UA (stdlib RobotFileParser.read() goes out
    as Python-urllib, which WAFs 403, and then treats that as disallow-all);
    per RFC 9309 an unavailable robots.txt (4xx/unreachable) is unrestricted —
    the authoritative robots/ToS review happens at onboarding anyway and is
    recorded in the spec's `verified` stamp."""
    host = urlsplit(url).netloc
    if host not in _robots_cache:
        rp: urllib.robotparser.RobotFileParser | None = None
        try:
            resp = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": ua}, timeout=TIMEOUT)
            if resp.ok:
                rp = urllib.robotparser.RobotFileParser(f"https://{host}/robots.txt")
                rp.parse(resp.text.splitlines())
            elif resp.status_code >= 500:
                log.warning("robots.txt unavailable for %s (%s); proceeding per onboarding review", host, resp.status_code)
        except requests.RequestException:
            log.warning("robots.txt unreachable for %s; proceeding per onboarding review", host)
        _robots_cache[host] = rp
    rp = _robots_cache[host]
    return True if rp is None else rp.can_fetch(ua, url)


def polite_get(url: str, *, headers: dict | None = None, timeout: int = TIMEOUT) -> requests.Response:
    """GET with our UA, >= HOST_SPACING_S between requests to the same host,
    and retries with backoff on 5xx/429/connection errors (other 4xx fail
    immediately — same shape as openmeteo._get)."""
    host = urlsplit(url).netloc
    wait = _last_hit.get(host, 0.0) + HOST_SPACING_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    last_err: Exception | None = None
    for attempt in range(RETRIES):
        _last_hit[host] = time.monotonic()
        try:
            resp = requests.get(url, headers=merged, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code != 429 and 400 <= code < 500:
                raise
            last_err = e
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
        if attempt < RETRIES - 1:
            time.sleep(5 * 2**attempt)
    assert last_err is not None
    raise last_err

"""Parser combinators: each resort adapter in the registry is one call.

All combinators return Callable[[str], float | None] taking the raw response
body and returning the snowfall value in the spec's unit, or None when the
report shows no number (off-season placeholder, page redesign). Raising is
fine too — scrape_all records the row as parse_failed either way.
"""

from __future__ import annotations

import json
import re
from typing import Callable

Parser = Callable[[str], "float | None"]

_NUMBER = r"-?\d+(?:[.,]\d+)?"


def _dig(obj, keys):
    for k in keys:
        obj = obj[int(k)] if isinstance(obj, list) else obj[k]
    return obj


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(_NUMBER, str(value))
    return float(m.group().replace(",", ".")) if m else None


def json_path(*keys: str | int) -> Parser:
    """Value at a key path in a JSON body (the site's own widget/API endpoint)."""

    def parse(body: str) -> float | None:
        return _to_float(_dig(json.loads(body), keys))

    return parse


def json_row(match_key: str, match_value: str, value_key: str) -> Parser:
    """Value from the first dict in a JSON list where match_key == match_value
    (e.g. pick one sector out of a per-sector conditions list)."""

    def parse(body: str) -> float | None:
        for row in json.loads(body):
            if isinstance(row, dict) and row.get(match_key) == match_value:
                return _to_float(row.get(value_key))
        return None

    return parse


def script_json(selector: str, *keys: str | int) -> Parser:
    """Key path inside an embedded JSON <script> (e.g. "#__NEXT_DATA__")."""

    def parse(body: str) -> float | None:
        from bs4 import BeautifulSoup

        tag = BeautifulSoup(body, "html.parser").select_one(selector)
        if tag is None or not tag.string:
            return None
        return _to_float(_dig(json.loads(tag.string), keys))

    return parse


def jsonld_value(*keys: str | int, type_filter: str | None = None) -> Parser:
    """Key path inside any <script type="application/ld+json"> block."""

    def parse(body: str) -> float | None:
        from bs4 import BeautifulSoup

        for tag in BeautifulSoup(body, "html.parser").find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except json.JSONDecodeError:
                continue
            for block in data if isinstance(data, list) else [data]:
                if type_filter and block.get("@type") != type_filter:
                    continue
                try:
                    return _to_float(_dig(block, keys))
                except (KeyError, IndexError, TypeError):
                    continue
        return None

    return parse


def css_number(selector: str, pattern: str = f"({_NUMBER})") -> Parser:
    """First regex group from the text of the first element matching a CSS
    selector."""

    def parse(body: str) -> float | None:
        from bs4 import BeautifulSoup

        el = BeautifulSoup(body, "html.parser").select_one(selector)
        if el is None:
            return None
        m = re.search(pattern, el.get_text(" ", strip=True))
        return float(m.group(1).replace(",", ".")) if m else None

    return parse


def regex_number(pattern: str) -> Parser:
    """First regex group anywhere in the raw body — last resort."""

    def parse(body: str) -> float | None:
        m = re.search(pattern, body, re.S)
        return float(m.group(1).replace(",", ".")) if m else None

    return parse

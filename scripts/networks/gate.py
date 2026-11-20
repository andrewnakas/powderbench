"""Apply a league's per-station NWP gate (data/gates/<league>.csv, written by
scripts/nwp_gate.py; see data/gates/README.md) inside a station generator."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def _rows(league: str) -> dict[str, dict]:
    path = ROOT / "data" / "gates" / f"{league}.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {r["station_id"]: r for r in csv.DictReader(f)}


def decide(league: str, station_id: str, default_offset: int) -> tuple[int | None, str]:
    """(day_offset to use, or None to drop; reason).

    A reading may be dated by its own day (the network's default offset) or
    by the day before (default - 1), never later: a best shift outside those
    two is a misdated record and drops the station."""
    r = _rows(league).get(station_id)
    if r is None:
        return default_offset, "not gated"
    k = int(r["suggested_offset"])
    rs = f"r {r['r_minus1']}/{r['r_0']}/{r['r_plus1']}"
    if r["verdict"] != "pass":
        return None, f"gate fail ({rs})"
    if k not in (default_offset - 1, default_offset):
        return None, f"gate: misdated, best shift needs offset {k} ({rs})"
    return k, f"gate pass, offset {k}" + (f" ({rs})" if k != default_offset else "")

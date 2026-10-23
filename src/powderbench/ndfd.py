"""NWS National Digital Forecast Database (NDFD) snow amount — the official
forecast that NWS forecast offices issue, i.e. the "human forecasters"
baseline. CONUS 2.5 km grid, public domain, from the NOAA Open Data bucket
(noaa-ndfd-pds, `wmo/snow/YYYY/MM/DD/YSUZ98_KWBN_<YYYYMMDDHHMM>`, refreshed
every ~30 min and archived).

Each file holds 6 h snow-amount periods ending at 00/06/12/18Z out to ~2.5
days. Round D uses the latest file posted before the run (3 h before the
cutoff), summed over the same windows as the WPC baseline:
  h24 <- 06Z D .. 06Z D+1,  h48 <- 06Z D .. 06Z D+2
h72 needs periods to 06Z D+3, beyond what the evening issuance reaches, so
NDFD is a 24/48 h baseline (like HRRR). Snow in the GRIB is metres.

Needs the optional `eccodes` dependency (pip install -e .[grib]).
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from . import HORIZONS
from .wpc import M_TO_IN, _download, _Grid

log = logging.getLogger(__name__)

BUCKET = "https://noaa-ndfd-pds.s3.amazonaws.com"
PRODUCT = "YSUZ98_KWBN"  # CONUS snow amount
WINDOW_START_UTC = 6  # station-local mountain midnight, as for WPC
PERIOD = timedelta(hours=6)


def _keys(day: date) -> list[tuple[datetime, str]]:
    prefix = f"wmo/snow/{day:%Y/%m/%d}/{PRODUCT}_"
    r = requests.get(f"{BUCKET}/?list-type=2&prefix={prefix}", timeout=60)
    r.raise_for_status()
    out = []
    for key in re.findall(r"<Key>([^<]+)</Key>", r.text):
        stamp = datetime.strptime(key.rsplit("_", 1)[1], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        out.append((stamp, key))
    return sorted(out)


def latest_key(as_of: datetime) -> str | None:
    """The newest NDFD snow file posted at or before `as_of`."""
    for day in (as_of.date(), as_of.date() - timedelta(days=1)):
        keys = [k for t, k in _keys(day) if t <= as_of]
        if keys:
            return keys[-1]
    return None


def _periods(path: Path, stations) -> dict[datetime, dict[str, float]]:
    """6 h period end (UTC) -> {station_id: snow metres}."""
    import eccodes

    out: dict[datetime, dict[str, float]] = {}
    grid = None
    with path.open("rb") as f:
        while (gid := eccodes.codes_grib_new_from_file(f)) is not None:
            try:
                grid = grid or _Grid(gid, stations)
                end = datetime.strptime(f"{eccodes.codes_get(gid, 'validityDate')}"
                                        f"{eccodes.codes_get(gid, 'validityTime'):04d}", "%Y%m%d%H%M")
                end = end.replace(tzinfo=timezone.utc)
                if end.hour % 6 or end.minute:  # the first, partial period of an off-hour issuance
                    continue
                values = eccodes.codes_get_values(gid)
                missing = eccodes.codes_get(gid, "missingValue")
                out[end] = {sid: float(values[i]) for sid, i in grid.index.items() if values[i] != missing}
            finally:
                eccodes.codes_release(gid)
    return out


def ndfd_prediction(stations, round_day: date, as_of: datetime | None = None) -> pd.DataFrame:
    """Submission rows for round D from the newest NDFD issuance before `as_of`."""
    try:
        import eccodes  # noqa: F401
    except ImportError:
        log.warning("eccodes not installed — skipping the NDFD baseline (pip install -e .[grib])")
        return pd.DataFrame()
    as_of = as_of or datetime.now(timezone.utc)
    key = latest_key(as_of)
    if key is None:
        log.warning("no NDFD snow file before %s", as_of)
        return pd.DataFrame()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "snow.grb"
        if not _download(f"{BUCKET}/{key}", dest):
            return pd.DataFrame()
        periods = _periods(dest, stations)

    start = datetime.combine(round_day, time(WINDOW_START_UTC), tzinfo=timezone.utc)
    rows = []
    for h in HORIZONS:
        ends = [start + PERIOD * (k + 1) for k in range(h // 6)]
        if not all(e in periods for e in ends):
            continue  # beyond this issuance's range
        for s in stations:
            vals = [periods[e].get(s.station_id) for e in ends]
            if any(v is None for v in vals):
                continue
            rows.append({"station_id": s.station_id, "horizon_h": h,
                         "snowfall_in": round(max(sum(vals), 0.0) * M_TO_IN, 2)})
    return pd.DataFrame(rows)

"""NRCS SNOLITE: depth-only snow telemetry (no snow pillow). Same AWDB API and
day convention as SNOTEL (periodRef END: SNWD(d) is the depth at the end of
local day d; snow24(d) = max(SNWD(d) - SNWD(d-1), 0)).

SNOTEL confirms a big depth jump with the station's own SWE. SNOLITE has no
SWE, so a >= 6" jump must instead be confirmed by a *neighbouring SNOTEL*
(within NEIGHBOR_KM): at least one must show an SWE rise >= 0.1" that day,
otherwise the day is voided as a likely sensor spike. Station measurements,
not a model, decide — so the QC can't tilt scoring toward the NWP baselines.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from .. import snotel, truth
from ..stations import load_stations

NEIGHBOR_KM = 40.0
MAX_NEIGHBORS = 3
QC_NO_NEIGHBOR_SUPPORT = "no_neighbor_swe_support"


def _km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def neighbors(station_ids: list[str]) -> dict[str, list[str]]:
    """Nearest SNOTEL stations (with SWE) within NEIGHBOR_KM of each SNOLITE site."""
    sites = {s.station_id: s for s in load_stations("stations") if s.station_id in set(station_ids)}
    snotel_sts = [s for s in load_stations("stations") if s.station_id.endswith(":SNTL")]
    out = {}
    for sid, s in sites.items():
        near = sorted((_km(s.latitude, s.longitude, t.latitude, t.longitude), t.station_id) for t in snotel_sts)
        out[sid] = [t for d, t in near[:MAX_NEIGHBORS] if d <= NEIGHBOR_KM]
    return out


def daily(station_ids: list[str], begin: date, end: date) -> pd.DataFrame:
    nb = neighbors(station_ids)
    obs = snotel.fetch_daily(station_ids, begin - timedelta(days=1), end, elements=("SNWD",))
    support_ids = sorted({t for ts in nb.values() for t in ts})
    sup = snotel.fetch_daily(support_ids, begin - timedelta(days=1), end, elements=("WTEQ",)) if support_ids else \
        pd.DataFrame(columns=["station_id", "date", "WTEQ"])
    # did a neighbour gain SWE that day?
    gained: set[tuple[str, date]] = set()
    for sid, g in sup.groupby("station_id"):
        w = g.set_index("date")["WTEQ"].sort_index()
        for d, inc in w.diff().items():
            if pd.notna(inc) and inc >= truth.SWE_SUPPORT_MIN_IN:
                gained.add((sid, d))

    rows = []
    for sid in station_ids:
        g = obs[obs["station_id"] == sid].set_index("date")["SNWD"] if len(obs) else pd.Series(dtype=float)
        for i in range((end - begin).days + 1):
            d = begin + timedelta(days=i)
            s0, s1 = g.get(d - timedelta(days=1)), g.get(d)
            flag = truth.QC_OK
            if s0 is None or s1 is None or pd.isna(s0) or pd.isna(s1):
                flag = truth.QC_MISSING
            elif s0 < 0 or s1 < 0 or s0 > truth.MAX_DEPTH_IN or s1 > truth.MAX_DEPTH_IN:
                flag = truth.QC_BAD_DEPTH
            else:
                delta = s1 - s0
                if delta > truth.MAX_PLAUSIBLE_SNOW24_IN:
                    flag = truth.QC_IMPLAUSIBLE
                elif delta >= truth.SWE_SUPPORT_SNOW_IN and not any((t, d) in gained for t in nb.get(sid, [])):
                    flag = QC_NO_NEIGHBOR_SUPPORT
            valid = flag == truth.QC_OK
            rows.append({"station_id": sid, "date": d, "snow24": max(s1 - s0, 0.0) if valid else float("nan"),
                         "valid": valid, "qc_flag": flag})
    return pd.DataFrame(rows, columns=["station_id", "date", "snow24", "valid", "qc_flag"])

"""Recover daily snow depths from Snowy Hydro's HYPLOT chart PDFs.

The daily Spencers Creek PDF (site 00003) is a vector chart. pdfminer gives
device-space geometry: the plot frame is the large LTRect (x: May 1 → end
date from the header, y: 0 → 300 cm) and the data series is the filled
LTCurve with the most vertices sitting on the frame's origin. Mapping curve
vertices through the frame axes recovers (date, depth_cm) to ~±2 cm — used
for reference columns only, never as scoring truth.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

import requests

log = logging.getLogger(__name__)

Y_AXIS_MAX_CM = 300.0
MIN_FRAME_W = 300
MIN_FRAME_H = 200


def _axis_window(text: str) -> tuple[date, date] | None:
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})", text)
    if not m:
        return None
    begin = datetime.strptime(m.group(1), "%d/%m/%Y").date()
    end = datetime.strptime(m.group(2), "%d/%m/%Y").date()
    return begin, end


def extract_daily_depths(url: str) -> dict[date, float]:
    """Fetch the HYPLOT PDF and return {date: depth_cm}."""
    from pdfminer.high_level import extract_pages, extract_text
    from pdfminer.layout import LTCurve, LTRect

    pdf = requests.get(url, timeout=60).content
    window = _axis_window(extract_text(io.BytesIO(pdf)))
    if window is None:
        log.warning("snowy pdf: no axis window in header text")
        return {}
    begin, end = window
    total_days = (end - begin).days
    if total_days <= 0:
        return {}

    frames: list[tuple[float, float, float, float]] = []
    curves = []
    for page in extract_pages(io.BytesIO(pdf)):
        for el in page:
            if isinstance(el, LTRect):
                x0, y0, x1, y1 = el.bbox
                if x1 - x0 >= MIN_FRAME_W and y1 - y0 >= MIN_FRAME_H:
                    frames.append(el.bbox)
            elif isinstance(el, LTCurve) and getattr(el, "fill", False):
                pts = getattr(el, "pts", None)
                if pts and len(pts) > 10:
                    curves.append(pts)
        break  # page 1 only
    if not frames or not curves:
        log.warning("snowy pdf: frame or data curve not found (%d frames, %d curves)", len(frames), len(curves))
        return {}
    # plot frame: smallest qualifying rect (page background is bigger)
    fx0, fy0, fx1, fy1 = min(frames, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    # data series: the filled curve anchored at the frame origin
    pts = max(
        (c for c in curves if abs(min(x for x, _ in c) - fx0) < 10),
        key=len,
        default=max(curves, key=len),
    )

    out: dict[date, float] = {}
    for x, y in pts:
        day = begin + timedelta(days=round((x - fx0) / (fx1 - fx0) * total_days))
        depth = max((y - fy0) / (fy1 - fy0) * Y_AXIS_MAX_CM, 0.0)
        if begin <= day <= end:
            out[day] = max(out.get(day, 0.0), round(depth, 1))
    return out

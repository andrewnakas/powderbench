"""Generate data/stations/jma.csv: JMA AMeDAS stations with an automatic
snow-depth sensor, for the japan league.

Candidates: every station reporting snow depth in JMA's live AMeDAS map
(amedastable + a map snapshot). Each is matched to its page in the
past-weather archive (prefecture page -> kind a/s + block_no, matched by
Japanese name, else nearest within 3 km). Stations near a ski area (OpenSkiData,
>= FEATURED_LIFTS lifts within FEATURED_KM) are featured.

Kept only when last winter's record passes the gate in `gate()`: enough
days with a value, and snowfall days that line up with the station's own
precipitation (catches dead or seasonal-only sensors without any external
model call).

    python scripts/networks/jma.py
"""

from __future__ import annotations

import csv
import html
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skiareas  # noqa: E402

OUT = ROOT / "data" / "stations" / "jma.csv"
TABLE = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
LATEST = "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt"
MAP = "https://www.jma.go.jp/bosai/amedas/data/map/{:%Y%m%d%H%M%S}.json"
PREF = "https://www.data.jma.go.jp/stats/etrn/select/prefecture.php"
UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com)"}
FEATURED_KM = 8.0
FEATURED_LIFTS = 5
FEATURED = {"Sukayu": "Hakkoda (Sukayu)"}  # big-snow site the lift rule misses (Hakkoda has a ropeway, few lifts)
MIN_DAYS = 100  # Dec-Mar days with a value last winter
MIN_SNOW_DAYS = 10
MIN_WET_SHARE = 0.8  # share of snowfall days with precipitation on the same day
FIELDS = ["station_id", "slug", "name", "resort", "region", "state", "elevation_ft", "latitude", "longitude",
          "league", "tier", "truth_source", "network", "external_id", "day_offset"]
HOKKAIDO = {"11": "Soya", "12": "Kamikawa", "13": "Rumoi", "14": "Ishikari", "15": "Sorachi", "16": "Shiribeshi",
            "17": "Okhotsk", "18": "Nemuro", "19": "Kushiro", "20": "Tokachi", "21": "Iburi", "22": "Hidaka",
            "23": "Oshima", "24": "Hiyama"}
PREFECTURES = {"31": "Aomori", "32": "Akita", "33": "Iwate", "34": "Miyagi", "35": "Yamagata", "36": "Fukushima",
               "40": "Ibaraki", "41": "Tochigi", "42": "Gunma", "43": "Saitama", "44": "Tokyo", "45": "Chiba",
               "46": "Kanagawa", "48": "Nagano", "49": "Yamanashi", "50": "Shizuoka", "51": "Aichi", "52": "Gifu",
               "53": "Mie", "54": "Niigata", "55": "Toyama", "56": "Ishikawa", "57": "Fukui", "60": "Shiga",
               "61": "Kyoto", "62": "Osaka", "63": "Hyogo", "64": "Nara", "65": "Wakayama", "66": "Okayama",
               "67": "Hiroshima", "68": "Shimane", "69": "Tottori", "71": "Tokushima", "72": "Kagawa",
               "73": "Ehime", "74": "Kochi", "81": "Yamaguchi", "82": "Fukuoka", "83": "Oita", "84": "Nagasaki",
               "85": "Saga", "86": "Kumamoto", "87": "Miyazaki", "88": "Kagoshima", "91": "Okinawa"}
_POINT = re.compile(r"viewPoint\(([^)]*)\)")


def _deg(d, m) -> float:
    return float(d) + float(m) / 60


def english(label: str) -> str:
    """OpenSkiData names often read 'ja, en, romaji (3 km)': keep the first ASCII part."""
    base, _, dist = label.partition(" (")
    parts = [p.strip() for p in base.split(",")]
    en = next((p for p in parts if p.isascii()), parts[0])
    return f"{en} ({dist}" if dist else en


def region(prec: str) -> str:
    return f"Hokkaido ({HOKKAIDO[prec]})" if prec in HOKKAIDO else PREFECTURES.get(prec, prec)


def prefecture_points(prec: str) -> list[dict]:
    """[{kind, block, kj, lat, lon, alt}] from one archive prefecture page."""
    r = requests.get(PREF, params={"prec_no": prec, "block_no": "", "year": "", "month": "", "day": "",
                                   "view": ""}, headers=UA, timeout=60)
    r.encoding = "utf-8"
    out, seen = [], set()
    for m in _POINT.finditer(r.text):
        f = [x.strip().strip("'") for x in m.group(1).split(",")]
        if len(f) < 9 or f[1] in seen or not f[1].isdigit():  # skips the JS function definition
            continue
        seen.add(f[1])
        out.append({"kind": f[0], "block": f[1], "kj": f[2], "lat": _deg(f[4], f[5]), "lon": _deg(f[6], f[7]),
                    "alt": float(f[8] or 0)})
    return out


def snow_stations() -> dict[str, dict]:
    table = requests.get(TABLE, headers=UA, timeout=60).json()
    latest = datetime.fromisoformat(requests.get(LATEST, headers=UA, timeout=60).text.strip())
    snap = latest.astimezone(timezone(timedelta(hours=9))).replace(minute=0, second=0)
    live = requests.get(MAP.format(snap), headers=UA, timeout=60).json()
    return {code: table[code] for code, v in live.items() if "snow" in v and code in table}


def gate(sid: str, ext: str, last: int) -> tuple[bool, str]:
    """Last winter (Dec-Mar): enough values, and snowfall days that are wet days."""
    import pandas as pd

    from powderbench.networks import jma

    kind, prec, block = ext.split(":")
    snow, wet = {}, {}
    for y, m in [(last - 1, 12), (last, 1), (last, 2), (last, 3)]:
        page = jma._get(kind, prec, block, y, m)
        snow.update({date(y, m, d): v for d, v in jma.parse_month(page, kind).items()})
        col = 3 if kind == "s" else 1  # precipitation total (mm)
        for row in jma._ROW.findall(page):
            cells = [html.unescape(jma._TAG.sub("", c)).strip() for c in jma._CELL.findall(row)]
            if len(cells) == jma.ROW_LEN[kind] and cells[0].isdigit():
                wet[date(y, m, int(cells[0]))] = jma.parse_value(cells[col])
    s = pd.Series(snow, dtype=float).dropna()
    days = len(s)
    snow_days = [d for d, v in s.items() if v >= 2]
    wet_share = (sum((wet.get(d) or 0) > 0 for d in snow_days) / len(snow_days)) if snow_days else 0
    ok = days >= MIN_DAYS and len(snow_days) >= MIN_SNOW_DAYS and wet_share >= MIN_WET_SHARE
    return ok, f"days={days} snow_days={len(snow_days)} wet={wet_share:.2f} total_cm={s.sum():.0f}"


def main() -> int:
    stations = snow_stations()
    by_prec: dict[str, list[str]] = {}
    for code in stations:
        by_prec.setdefault(code[:2], []).append(code)
    rows = []
    for prec, codes in sorted(by_prec.items()):
        points = prefecture_points(prec)
        for code in codes:
            t = stations[code]
            lat, lon = _deg(*t["lat"]), _deg(*t["lon"])
            match = [p for p in points if p["kj"] == t["kjName"]]
            if not match:
                near = sorted(points, key=lambda p: skiareas.km(lat, lon, p["lat"], p["lon"]))
                match = [p for p in near[:1] if skiareas.km(lat, lon, p["lat"], p["lon"]) <= 3]
            if not match:
                print(f"  no archive page for {code} {t['enName']}")
                continue
            p = match[0]
            area, d = skiareas.nearest(lat, lon, FEATURED_LIFTS)
            featured = (area is not None and d <= FEATURED_KM) or t["enName"] in FEATURED
            rows.append({"station_id": f"{code}:JP:JMA", "slug": f"jma-{code}", "name": t["enName"],
                         "resort": FEATURED.get(t["enName"]) or english(
                             skiareas.label(t["enName"], lat, lon, within_km=FEATURED_KM, min_lifts=FEATURED_LIFTS)),
                         "region": region(prec), "state": "JP", "elevation_ft": round(t["alt"] * 3.28084),
                         "latitude": round(lat, 5), "longitude": round(lon, 5), "league": "japan",
                         "tier": "featured" if featured else "network", "truth_source": "jma",
                         "network": "JMA AMeDAS", "external_id": f"{p['kind']}:{prec}:{p['block']}",
                         "day_offset": 0})
    today = date.today()
    last = today.year if today.month >= 4 else today.year - 1
    kept = []
    for r in rows:
        ok, why = gate(r["station_id"], r["external_id"], last)
        print(f"{r['station_id']:>14} {r['name']:<24} {r['tier']:<8} {why} -> {'keep' if ok else 'drop'}")
        if ok:
            kept.append(r)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: r["name"]))
    print(f"{len(stations)} snow stations -> {len(rows)} matched -> {len(kept)} kept "
          f"({sum(r['tier'] == 'featured' for r in kept)} featured) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The resort registry: every scraped site, with its consent audit stamp.

Onboarding a resort (docs/RESORTS.md): recon the snow-report page, find the
site's OWN endpoint (never an aggregator), review robots.txt + ToS, write the
parser + a fixture test, record the outcome in `verified`, then set
`enabled=True` and add the station to data/stations.yaml. Entries that failed
review stay here disabled with the outcome in `notes` so nobody re-litigates
them.
"""

from __future__ import annotations

import re

from .parse import css_number, json_path, json_row, labeled_number, regex_number, table_cell
from .spec import ResortSpec

# Vía Bariloche's BusPlus API serves exactly three centros: CA (Catedral),
# CH (Chapelco), LH (La Hoya) — its 404 enumerates them. Browser public key
# ships in each resort's page JS. Responses end with a sector-less route/
# avalanche object, so parsers must match sectors by name, never by index.
_BUSPLUS = "https://ws.busplus.com.ar/centrosesqui/partediario/climas?Centro={code}"
_BUSPLUS_KEY = {"PUBLIC-KEY": "ij877HGyh74U&mmwsYH"}


def _mt_hood_meadows_parse(body: str) -> float | None:
    """skihood.com's report API lists snow zones (Base Area, Mid-Mountain);
    match the zone by name, never by index. Value in centimetres."""
    import json

    zones = json.loads(body)["data"]["snow"]["snowZones"]
    for z in zones:
        if z.get("name") == "Mid-Mountain":
            v = (z.get("freshSnowFallDepth24H") or {}).get("value")
            return None if v is None else float(v)
    return None


_NUMBER = r"\d+(?:\.\d+)?"


def _reading(raw: str | None) -> float | None:
    """A snow-report cell -> number. 'Trace' -> 0 (as for observer networks);
    a range ('2"-4"') -> its midpoint (the honest point estimate; the upper
    bound would bias high); '4"+' -> 4; 'N/A' or blank -> None."""
    if raw is None:
        return None
    text = re.sub(r"<[^>]+>|&[a-z]+;|&#\d+;", " ", raw).strip()
    if re.fullmatch(r"(?i)(trace|tr|t)\.?", text):
        return 0.0
    nums = re.findall(_NUMBER, text)
    if not nums:
        return None
    if len(nums) >= 2 and re.search(r"\d\s*(?:\"|”|″)?\s*(?:-|–|to)\s*\d", text):
        return round((float(nums[0]) + float(nums[1])) / 2, 2)
    return float(nums[0])


def _cell(pattern: str):
    """Regex whose group 1 is the raw report cell, read with _reading."""

    def parse(body: str) -> float | None:
        m = re.search(pattern, body, re.S)
        return _reading(m.group(1)) if m else None

    return parse


def _stamp(pattern: str, fmt: str, tz: str):
    """report_time from a page's own 'Updated …' text: regex group 1, parsed
    with `fmt` (a.m./p.m. and case normalised) in the resort's zone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def parse(body: str) -> str | None:
        m = re.search(pattern, body, re.S)
        if not m:
            return None
        text = re.sub(r"\s+", " ", m.group(1).replace("a.m.", "AM").replace("p.m.", "PM")).strip()
        text = re.sub(r"\b(am|pm)\b", lambda x: x.group(1).upper(), text)
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo(tz)).isoformat()
        except ValueError:
            return None

    return parse


def _red_mountain_parse(body: str) -> float | None:
    """api.redresort.com/report/full: the patrol's MountainReport. Off-season
    it keeps last April's numbers with SummerMode=true -> no report."""
    import json

    rep = json.loads(body).get("MountainReport") or {}
    if rep.get("SummerMode"):
        return None
    v = rep.get("Metric24hours")
    return None if v is None else float(v)


def _red_mountain_time(body: str) -> str | None:
    import json

    return (json.loads(body).get("MountainReport") or {}).get("DateTime")


_NA_REVIEW = "2026-10-20: robots.txt allows the page (PowderBench UA); "


def _whakapapa_parse(body: str) -> float | None:
    """whakapapa.com/report ships its snow report as one large base64
    app-state blob inside the SSR HTML; the fixture stores the decoded
    subtree directly, so accept either form. Reads snow24Hours at the
    'Top of Knoll T-bar' location (upper mountain)."""
    import base64
    import json
    import re

    text = body.lstrip()
    if not text.startswith("{"):
        blobs = re.findall(r"[A-Za-z0-9+/=]{400,}", body)
        if not blobs:
            return None
        decoded = base64.b64decode(max(blobs, key=len)).decode("utf-8", errors="replace")
        start, end = decoded.find("{"), decoded.rfind("}")
        if start < 0 or end <= start:
            return None
        text = decoded[start : end + 1]
    locations = (
        json.loads(text)
        .get("report", {})
        .get("whakapapa", {})
        .get("currentConditions", {})
        .get("resortLocations", {})
        .get("location", [])
    )
    pick = next((l for l in locations if l.get("name") == "Top of Knoll T-bar"), None)
    pick = pick or (locations[0] if locations else None)
    if pick is None or pick.get("snow24Hours") in (None, ""):
        return None
    return float(pick["snow24Hours"])

# NZSki (Coronet Peak / The Remarkables / Mt Hutt) serve one JSON per resort
# from their own Azure Front Door — the same file their site's weather app
# fetches. No 24h field is published, so daily snowfall is derived from
# day-over-day deltas of snow.seasonTotal (cumulative=True).
_NZSKI = "https://webcams-awb2e0ceg7cccsba.a02.azurefd.net/{slug}-data.json"
_NZSKI_VERIFIED = (
    "2026-07-10: nzski.com robots.txt allows (only /aspnet_client/, /bin/ "
    "disallowed; CDN host has no robots.txt); NZSki Terms & Conditions "
    "reviewed — no scraping/automated-access clause; endpoint = NZSki's own "
    "weather-app JSON on azurefd.net"
)


def _not_scraped(body: str) -> float | None:
    return None


REGISTRY: tuple[ResortSpec, ...] = (
    # ---- live ----
    ResortSpec(
        resort_id="mt-hutt",
        station_id="mt-hutt:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url=_NZSKI.format(slug="mt-hutt"),
        parse=json_path("snow", "seasonTotal"),
        unit="cm",
        cumulative=True,
        report_hour_local=8,
        enabled=True,
        verified=_NZSKI_VERIFIED,
        notes="snow.seasonTotal (cm, cumulative); base depths also available.",
    ),
    ResortSpec(
        resort_id="coronet-peak",
        station_id="coronet-peak:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url=_NZSKI.format(slug="coronet-peak-winter"),
        parse=json_path("snow", "seasonTotal"),
        unit="cm",
        cumulative=True,
        report_hour_local=8,
        enabled=True,
        verified=_NZSKI_VERIFIED,
        notes="Winter feed is coronet-peak-winter-data.json (plain coronet-peak goes stale off-season).",
    ),
    ResortSpec(
        resort_id="remarkables",
        station_id="remarkables:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url=_NZSKI.format(slug="the-remarkables"),
        parse=json_path("snow", "seasonTotal"),
        unit="cm",
        cumulative=True,
        report_hour_local=8,
        enabled=True,
        verified=_NZSKI_VERIFIED,
        notes="Feed slug is the-remarkables; updates can lag a few days in dry spells.",
    ),
    ResortSpec(
        resort_id="thredbo",
        station_id="thredbo:AU:RESORT",
        country="AU",
        tz="Australia/Sydney",
        url="https://www.thredbo.com.au/feeds/snow-report/",
        parse=regex_number(r'snow24Hours amount="([\d.,]+)"'),
        unit="cm",
        report_hour_local=7,
        enabled=True,
        verified=(
            "2026-07-10: robots.txt allows all; Thredbo terms reviewed — no "
            "scraping/automated-access clause; endpoint = Thredbo's own XML "
            "snow-report feed (/feeds/snow-report/, units=metric)"
        ),
        notes="Literal snow24Hours field; feed refreshes ~06:50 AEST. snow48/72Hours, season, base also present.",
    ),
    ResortSpec(
        resort_id="catedral",
        station_id="catedral:AR:RESORT",
        country="AR",
        tz="America/Argentina/Buenos_Aires",
        url=_BUSPLUS.format(code="CA"),
        parse=json_row("NombreSector", "Sector Superior", "nieveUltimas24"),
        unit="cm",
        report_hour_local=8,
        enabled=True,
        verified=(
            "2026-07-10: catedralaltapatagonia.com robots.txt allows all; "
            "terms reviewed — no scraping/automated-access clause; endpoint = "
            "parte-de-nieve API of the operator's own platform (Vía Bariloche "
            "/ busplus.com.ar) using the public browser key their page ships"
        ),
        headers=_BUSPLUS_KEY,
        notes="nieveUltimas24 for Sector Superior (2000 m); Base/Intermedio sectors also available.",
    ),
    ResortSpec(
        resort_id="chapelco",
        station_id="chapelco:AR:RESORT",
        country="AR",
        tz="America/Argentina/Buenos_Aires",
        url=_BUSPLUS.format(code="CH"),
        parse=json_row("NombreSector", "Sector CUMBRE", "nieveUltimas24"),
        unit="cm",
        report_hour_local=9,
        enabled=True,
        verified=(
            "2026-07-10: same operator platform as catedral (BusPlus API, browser "
            "public key from their own page JS); API host has no robots.txt; no "
            "ToS scraping clause found; chapelco.com.ar is the real resort domain"
        ),
        headers=_BUSPLUS_KEY,
        notes="nieveUltimas24 for Sector CUMBRE (2000 m); sector names are UPPERCASE for this centro.",
    ),
    ResortSpec(
        resort_id="la-hoya",
        station_id="la-hoya:AR:RESORT",
        country="AR",
        tz="America/Argentina/Buenos_Aires",
        url=_BUSPLUS.format(code="LH"),
        parse=json_row("NombreSector", "Sector Superior", "nieveUltimas24"),
        unit="cm",
        report_hour_local=9,
        enabled=True,
        verified=(
            "2026-07-10: third centro on the BusPlus API (its 404 enumerates "
            "CA/CH/LH); API host has no robots.txt; no ToS scraping clause found"
        ),
        headers=_BUSPLUS_KEY,
        notes="La Hoya (Esquel, Chubut) — bonus resort the operator API exposes; Sector Superior (1800 m).",
    ),
    ResortSpec(
        resort_id="portillo",
        station_id="portillo:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://skiportillo.com/montana/clima-y-condiciones/",
        parse=labeled_number("Nieve a caída a la Fecha:", up=2),
        unit="cm",
        cumulative=True,
        report_hour_local=9,
        enabled=True,
        verified=(
            "2026-07-10: robots.txt allows all (Yoast default); no ToS page "
            "found on site; conditions table is server-rendered WordPress on "
            "their own page (wp-json ACF is empty — HTML is the source)"
        ),
        notes="Season-to-date 'Nieve a caída a la Fecha' (Hotel area) -> daily deltas; base/plateau depths also present.",
    ),
    ResortSpec(
        resort_id="valle-nevado",
        station_id="valle-nevado:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://www.vallenevado.com/es/reporte-de-montana/",
        parse=labeled_number("Nieve caída 24 horas", up=3),
        unit="cm",
        report_hour_local=14,
        enabled=True,
        verified=(
            "2026-07-10: robots.txt allows all; /es/legal reviewed — no "
            "scraping clause; report is server-rendered on their own page "
            "(no aggregator feed detected)"
        ),
        notes="Mountain-wide 24h value ('0cm / 0in' format); page updates intraday ~14:00 CLT, so the 20:30 UTC scrape is the fresh capture. "
        "2026-10-20 re-check: owner Mountain Capital Partners bans robots on brianhead.com, but vallenevado.com's own "
        "/es/legal/ page and 2026 Terminos y Condiciones PDF contain no robots/scraping/reproduction clause.",
    ),
    ResortSpec(
        resort_id="antillanca",
        station_id="antillanca:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://antillanca.cl/wp-json/antillanca/v1/parte-diario",
        parse=json_path("nieve_24h"),
        unit="cm",
        report_hour_local=9,
        enabled=True,
        verified=(
            "2026-07-10: first-party WordPress REST endpoint (wp-json not "
            "disallowed by robots); no ToS page found; weather companion "
            "endpoint is their own station (the snow-forecast link is only "
            "their forecast tab, not this parte)"
        ),
        notes="Cleanest source of the batch: nieve_24h plus depths/quality/updated (~08:40 CLT).",
    ),
    ResortSpec(
        resort_id="las-lenas",
        station_id="las-lenas:AR:RESORT",
        country="AR",
        tz="America/Argentina/Mendoza",
        url="https://laslenas.com/estado-pistas/condiciones-del-tiempo/",
        parse=table_cell("CUMBRE", "PRECIPITADA"),
        unit="cm",
        report_hour_local=9,
        enabled=True,
        verified=(
            "2026-07-10: robots.txt allows all (Yoast); no ToS page (privacy "
            "policy only) — no scraping clause; server-rendered WP table on "
            "their own page"
        ),
        notes=(
            "'PRECIPITADA ÚLTIMAS 24H' for CUMBRE. Cells were all '-' on 2026-07-10 "
            "(parses as no_report) — self-activates if they resume filling the table."
        ),
    ),
    ResortSpec(
        resort_id="turoa",
        station_id="turoa:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url="https://www.pureturoa.nz/snow-report",
        parse=labeled_number("Last 24hrs"),
        unit="cm",
        report_hour_local=9,
        enabled=True,
        verified=(
            "2026-07-11: pureturoa.nz robots.txt has no rules (sitemap only); "
            "terms reviewed — no scraping/automated-access clause; page is "
            "Webflow server-rendered, values in HTML (no widget API)"
        ),
        notes="'Last 24hrs' cm from the snow grid; depth measured on the Alpine meadow / top of the Giant. Updates ~08:56 NZT.",
    ),
    ResortSpec(
        resort_id="whakapapa",
        station_id="whakapapa:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url="https://www.whakapapa.com/report",
        parse=_whakapapa_parse,
        unit="cm",
        report_hour_local=10,
        enabled=True,
        verified=(
            "2026-07-11: robots.txt allows /report (disallows /faq, /search, "
            "ecom paths); terms reviewed — no scraping/automated-access "
            "clause; data is a base64 app-state blob embedded in their own "
            "SSR page (the /snow-report path is a dead shell — use /report)"
        ),
        notes="snow24Hours at 'Top of Knoll T-bar'; seasonTotal/base also present. Report updates ~10:00 NZT.",
    ),
    # ---- reviewed, unusable ----
    ResortSpec(
        resort_id="cardrona",
        station_id="cardrona:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url="https://www.cardrona.com/winter/snow-report/",
        parse=_not_scraped,
        notes="2026-07-10 UNUSABLE: snow data on cardrona.com is aggregator-fed (useDataFromOpenSnow) — off-limits.",
    ),
    ResortSpec(
        resort_id="treble-cone",
        station_id="treble-cone:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url="https://www.treblecone.com/winter/snow-report/",
        parse=_not_scraped,
        notes="2026-07-10 UNUSABLE: same platform as cardrona.com, aggregator-fed (useDataFromOpenSnow).",
    ),
    ResortSpec(
        resort_id="perisher",
        station_id="perisher:AU:RESORT",
        country="AU",
        tz="Australia/Sydney",
        url="https://www.perisher.com.au/",
        parse=_not_scraped,
        notes=(
            "2026-07-10 UNUSABLE (conservative): Vail Resorts-owned; site is a JS shell and the terms could "
            "not be fetched statically, and Vail's standard Terms of Use prohibit automated access — treated "
            "as prohibited pending manual review. Spencers Creek obsfeed covers this massif instead."
        ),
    ),
    ResortSpec(
        resort_id="falls-creek",
        station_id="falls-creek:AU:RESORT",
        country="AU",
        tz="Australia/Melbourne",
        url="https://www.fallscreek.com.au/",
        parse=_not_scraped,
        notes="2026-07-10 UNUSABLE (conservative): Vail Resorts-owned, same reasoning as perisher.",
    ),
    ResortSpec(
        resort_id="hotham",
        station_id="hotham:AU:RESORT",
        country="AU",
        tz="Australia/Melbourne",
        url="https://www.mthotham.com.au/",
        parse=_not_scraped,
        notes="2026-07-10 UNUSABLE (conservative): Vail Resorts-owned, same reasoning as perisher.",
    ),
    # ---- recon incomplete (disabled until someone finds a stable endpoint) ----
    ResortSpec(
        resort_id="mt-buller",
        station_id="mt-buller:AU:RESORT",
        country="AU",
        tz="Australia/Melbourne",
        url="https://api.mtbuller.com.au/api/weather/widget",
        parse=json_path("snow_report", "snow_last_24_hours"),
        notes=(
            "2026-07-11 DISABLED pending permission: clean public JSON API found "
            "(api.mtbuller.com.au/api/weather/widget, snow_report.snow_last_24_hours, cm, "
            "robots allow-all), but their terms say site material 'may not otherwise be used, "
            "copied, reproduced, published, distributed … without prior written approval of "
            "Mt Buller'. No explicit automated-access clause; still, we publish values, so "
            "keep disabled until they OK it. Fixture saved for the day they do."
        ),
    ),
    ResortSpec(
        resort_id="castor",
        station_id="castor:AR:RESORT",
        country="AR",
        tz="America/Argentina/Ushuaia",
        url="https://www.cerrocastor.com/es_ar/estado-pistas-medios.html",
        parse=_not_scraped,
        notes=(
            "2026-07-10 UNUSABLE for 24h snowfall: server-rendered page publishes per-sector "
            "depth only (span.lbl_espesornieve), no 24h/overnight field anywhere. Depth-delta "
            "mode is possible later if wanted; robots allows the page, no ToS scraping clause."
        ),
    ),
    ResortSpec(
        resort_id="caviahue",
        station_id="caviahue:AR:RESORT",
        country="AR",
        tz="America/Argentina/Buenos_Aires",
        url="https://www.caviahue.com/partediario",
        parse=_not_scraped,
        notes="2026-07-10 recon incomplete: Wix site's parte-diario page currently 404s (removed/renamed). Wix server-renders content, so recheck when restored.",
    ),
    ResortSpec(
        resort_id="el-colorado",
        station_id="el-colorado:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://elcolorado.cl/pistas/",
        parse=_not_scraped,
        notes="2026-07-10 UNUSABLE: site publishes lift/piste percentages and a weather line only — no snowfall numbers at all.",
    ),
    ResortSpec(
        resort_id="la-parva",
        station_id="la-parva:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://laparva.cl/es/reporte-de-montana/condiciones-de-montana/",
        parse=_not_scraped,
        notes=(
            "2026-07-10 DISABLED (stale): clean server-rendered report structure "
            "(snow-data-item value/label pairs, 2600 m) but the footer date reads 19-01-2026 "
            "with all zeros — six months old. Enabling needs a freshness gate on the footer "
            "date plus the resort actually resuming updates."
        ),
    ),
    ResortSpec(
        resort_id="nevados-chillan",
        station_id="nevados-chillan:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://www.nevadosdechillan.com/reporte-montana",
        parse=_not_scraped,
        notes="2026-07-10 recon incomplete: Laravel Livewire app, report renders client-side.",
    ),
    ResortSpec(
        resort_id="corralco",
        station_id="corralco:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://corralco.com/montana/",
        parse=_not_scraped,
        notes="2026-07-10 recon incomplete: no live daily snow report found on the site (marketing copy only).",
    ),

    # ---- North America (resorts-na league; scraped 16:30 + 20:30 UTC) ----
    ResortSpec(
        resort_id="alta",
        station_id="alta:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://www.alta.com/weather",
        parse=regex_number(r'"last24":"([\d.]+)"'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified=_NA_REVIEW + "only /api/, /sumo/, /banzai/ disallowed. No terms of use published "
        "(/terms, /terms-of-use, /legal 404); privacy policy has no automated-access clause. Value = "
        "Alta's own server-rendered conditions block (dated_at 07:00), separate from the OpenSnow forecast widget.",
        notes="Alta snow-study 24h new snow (window.Alta conditions.last24); year_to_date also present.",
    ),
    ResortSpec(
        resort_id="mt-baker",
        station_id="mt-baker:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.mtbaker.us/snow-report/",
        parse=css_number(".conditions-stat_snow_24"),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified=_NA_REVIEW + "only privacy/cookie policies exist, no scraping clause. First-party "
        "WordPress snow-report block (Feb 2026 snapshot: 24h 14\", 'Last Updated 8:12 AM').",
        notes="Upper reference = Pan Dome (5,020 ft). Block is absent off-season -> no_report.",
    ),
    ResortSpec(
        resort_id="timberline",
        station_id="timberline:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://timberlinelodge.com/conditions/",
        parse=regex_number(r'conditions__item-data"?>\s*(\d+(?:\.\d+)?)\s*<span[^>]*>[^<]*</span>\s*</div>'
                           r'\s*<div class="?conditions__item-label"?>\s*24 Hours'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified=_NA_REVIEW + "Crawl-delay 10 (we fetch twice a day). No terms page (404); privacy and "
        "hotel policies have no scraping clause. First-party conditions block (Snow-Forecast only linked).",
        notes="Measured at the lodge (6,000 ft). Pattern anchored on the '24 Hours' label cell — the naive "
        "label parser read the '24' in the label itself.",
    ),
    ResortSpec(
        resort_id="mt-hood-meadows",
        station_id="mt-hood-meadows:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.skihood.com/api/weather/report?includeResortInfo=true&includeSnowInfo=true",
        parse=_mt_hood_meadows_parse,
        unit="cm",
        report_hour_local=6,
        enabled=True,
        verified=_NA_REVIEW + "robots allows all. Only skier 'rules of use' and privacy pages exist — no "
        "scraping/republication clause. Endpoint on the resort's own domain; schema resembles a resort-data "
        "vendor (Lumiplan-style), i.e. the resort's own entry tool, not an aggregator.",
        notes="Mid-Mountain (6,546 ft) zone, matched by name.",
    ),
    ResortSpec(
        resort_id="sugar-bowl",
        station_id="sugar-bowl:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.sugarbowl.com/conditions",
        parse=css_number('[data-update-id="conditions_snow_24hr_summit"]'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified=_NA_REVIEW + "footer 'Terms & Conditions' link is a 404; privacy policy has no scraping "
        "clause. First-party server-rendered conditions (data-update-id spans).",
        notes="Summit 24h new snow; base and YTD spans also present.",
    ),
    ResortSpec(
        resort_id="loveland",
        station_id="loveland:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://skiloveland.com/snow-report/",
        parse=regex_number(r'id="report-5-24-hour"[^>]*>\s*(\d+(?:\.\d+)?)'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified=_NA_REVIEW + "robots 'Disallow:' empty. No terms or privacy pages (404). Hand-entered "
        "first-party report table; OpenSnow supplies only the separate forecast widget.",
        notes="The 5:00 AM report's 24 HOUR cell (an 8:00 AM update follows; earliest scrape wins).",
    ),
    ResortSpec(
        resort_id="telluride",
        station_id="telluride:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://tellurideskiresort.com/snow-report/",
        parse=regex_number(r'Last 24 hours</span>\s*<span>\s*<strong>\s*(\d+(?:\.\d+)?)'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified=_NA_REVIEW + "Crawl-delay 10. No terms page (404); privacy policy has no scraping clause. "
        "Value from the resort's own tsr-report totals block; 'Powered by OpenSnow' labels only the "
        "embedded forecast post.",
        notes="Last 24 hours (tsr-report-app-snow-totals).",
    ),
    ResortSpec(
        resort_id="whitefish",
        station_id="whitefish:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://skiwhitefish.com/snowreport/",
        parse=regex_number(r'<h5[^>]*>\s*(\d+(?:\.\d+)?)\s*(?:"|&quot;|&#8243;|&#8221;|”|″)\s*</h5>\s*<p[^>]*>\s*Past 24 Hours'),
        unit="in",
        notes="2026-10-20 rejected: parent Foley Entertainment Group's terms (now reachable) list 'Winter Sports Inc. – DBA Whitefish Mountain Resort' and ban 'any robot, spider, or other automatic device … including monitoring or copying'. "
        "Parser kept for reference (Wayback 24h 10\" vs 7-day 25\").",
    ),
    ResortSpec(
        resort_id="mt-rose",
        station_id="mt-rose:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://skirose.com/snow-report/",
        parse=_not_scraped,
        notes="2026-10-20 rejected for accuracy: 'New Snow' means since the last report (not a fixed 24h) "
        "and can be a range.",
    ),
    ResortSpec(
        resort_id="jackson-hole",
        station_id="jackson-hole:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://jacksonhole-prod.zaneray.com/api/snow.json",
        parse=_not_scraped,
        notes="2026-10-20 held: clean first-party JSON (Bridger-Teton study plots) but terms forbid "
        "retransmitting/redistributing content without written permission — on the outreach list.",
    ),
    ResortSpec(
        resort_id="snowbird",
        station_id="snowbird:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://api.snowbird.com/api/v1/dor/drupal/snow-reports",
        parse=_not_scraped,
        notes="2026-10-20 rejected: POWDR terms ban automated access (also Copper, Eldora, Killington, Mt Bachelor).",
    ),
    ResortSpec(
        resort_id="brighton",
        station_id="brighton:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://www.brightonresort.com/api/reportpal?resortName=br&useReportPal=true",
        parse=_not_scraped,
        notes="2026-10-20 rejected: Boyne terms ban scraping (also Big Sky, Sugarloaf, Sunday River).",
    ),
    ResortSpec(
        resort_id="mammoth",
        station_id="mammoth:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.mammothmountain.com/",
        parse=_not_scraped,
        notes="2026-10-20 rejected: Alterra terms ban bots/scraping (also Palisades, Solitude, Crystal, "
        "Steamboat, Winter Park, Sugarbush, Tremblant, A-Basin). Vail resorts rejected by policy.",
    ),
    ResortSpec(
        resort_id="sierra-at-tahoe",
        station_id="sierra-at-tahoe:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.sierraattahoe.com/weather-snow-report/",
        parse=_not_scraped,
        notes="2026-10-20 rejected: snow summary 'Powered by OpenSnow' (aggregator-fed).",
    ),
    ResortSpec(
        resort_id="lake-louise",
        station_id="lake-louise:CA:RESORT",
        country="CA",
        tz="America/Edmonton",
        url="https://www.skilouise.com/snow-conditions/",
        parse=_cell('Upper Mountain Snowfall</b>.{0,4000}?<h4 class="obj-text active" data-switch="c">\\s*<strong[^>]*>([^<]{0,40})</strong>\\s*<small class="small">cm</small>\\s*</h4>\\s*<h4 class="obj-text d-none" data-switch="f">\\s*<strong[^>]*>[^<]*</strong>\\s*<small class="small">in</small>\\s*</h4>\\s*<p>\\s*<span class="obj-text d-block">\\s*Last 24 Hours'),
        unit="cm",
        report_hour_local=5,
        enabled=True,
        verified="2026-10-20: " + "robots 'Disallow:' empty. No terms of use (/terms, /terms-of-use, /legal redirect home); privacy policy and SMS terms have no scraping clause. First-party server-rendered, hand-entered snowfall blocks (Upper/Mid/Lower); RWDI supplies only the forecast. Wayback 2026-02-17: Upper 24h 12 cm (overnight 12, 48h 13, 7d 24, stamped 4:50 am); 2026-03-01: 14 (48h 24, 7d 42).",
        notes="Upper Mountain 'Last 24 Hours' (cm).",
    ),
    ResortSpec(
        resort_id="marmot-basin",
        station_id="marmot-basin:CA:RESORT",
        country="CA",
        tz="America/Edmonton",
        url="https://www.skimarmot.com/mountain/weather-conditions/",
        parse=_cell('<div class="weather-key">Last 24 Hours</div>\\s*<div class="weather-value">([^<]{0,40})</div>'),
        unit="cm",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + "robots disallows only /clientadmin. No terms of use (404); privacy policy has no scraping clause. First-party server-rendered snow-report panel; RWDI supplies only the forecast. Wayback 2025-03-27: Last 24 Hours 6 cm (base 112, season 277, 'Updated … 6:55 a.m.'); 'Trace' reads as 0.",
        notes="Single 'Last 24 Hours' figure; no 48h/7d on the page.",
    ),
    ResortSpec(
        resort_id="le-massif",
        station_id="le-massif:CA:RESORT",
        country="CA",
        tz="America/Toronto",
        url="https://www.lemassif.com/en/the-mountain/winter/snow-weather-webcams",
        parse=_cell('<div class="t-large f-w-bold">\\s*24 Hours\\s*</div>\\s*<div class="t-c-aqua-light value">\\s*<span class="metric-value">([^<]{0,40})<span class="unit">\\s*cm'),
        unit="cm",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + "Drupal default robots (page allowed). No website terms of use (404); /en/terms_conditions covers tickets/passes only. First-party Snowfall block (Since 6am / 24h / 48h / 7d / season); snow-forecast.com supplies only a separate forecast table. Wayback 2026-01-17: 24h 3 cm (48h 15, 7d 26, 'Updated: Jan 17 - 6:00am').",
        notes='24 Hours (cm).',
    ),
    ResortSpec(
        resort_id="jay-peak",
        station_id="jay-peak:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://digital.jaypeakresort.com/conditions/snow-report/snow-report/",
        parse=_cell('<dt>\\s*Last 24 Hours\\s*</dt>\\s*<dd>([^<]{0,60})</dd>'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified="2026-10-20: " + 'robots allows the page and the iframe host (digital.jaypeakresort.com: Allow: /). No terms of use published (404); /resort/policies and privacy policy have no scraping clause; parent Pacific Group Resorts publishes none. First-party SnowReport block on the resort\'s own subdomain. Wayback 2025-12-16: 24h \'2"-4"\' (48h 6, 7d 46); 2026-03-05: 0 / 1 / 5.',
        notes="Ranges read as the midpoint ('2-4' -> 3).",
    ),
    ResortSpec(
        resort_id="bolton-valley",
        station_id="bolton-valley:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://snow.boltonvalley.com/snow-report/",
        parse=_cell('<dd>([^<]{0,60})</dd>\\s*<dt>\\s*Last 24 Hours\\s*</dt>'),
        unit="in",
        report_hour_local=9,
        enabled=True,
        verified="2026-10-20: " + 'robots: snow.boltonvalley.com Allow: / (Crawl-delay 10; we fetch twice a day). No terms of use (404); footer links only privacy/accessibility. First-party snow-report subdomain (same platform as Jay Peak). Wayback 2026-01-23: 24h \'3-5"\' (48h 10, 7d 10); 2026-02-17: 0 / 0 / 11.',
        notes="Ranges read as the midpoint ('3-5' -> 4).",
    ),
    ResortSpec(
        resort_id="wolf-creek",
        station_id="wolf-creek:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://wolfcreekski.com/wcs-snow-report/",
        parse=_cell('snow-report-left-text"[^>]*>\\s*24 hours\\s*</div>\\s*<div[^>]*snow-report-right-text[^>]*>\\s*<span[^>]*>([^<]{0,40})</span>'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + "robots 'Disallow:' empty. No terms of use (/terms, /terms-of-use, /terms-and-conditions, /legal, /terms-of-service all 404). Wolf Creek's own patroller-signed snow report ('Updated: <date> - <time>'). Wayback 2026-02-11: 24h 2 (48h 2, 72h 2); 2026-02-15: 0 (48h 8).",
        notes='Every report is also archived as a WordPress post (snowreport sitemap) — a possible backfill source.',
    ),
    ResortSpec(
        resort_id="brundage",
        station_id="brundage:US:RESORT",
        country="US",
        tz="America/Boise",
        url="https://brundage.com/on-the-mountain/snow-report/",
        parse=_cell('Lakeview \\(7,400&#039;\\)</div>\\s*<ul>\\s*<li>24 Hours:([^<]{0,40})</li>'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty. No terms of use (404); privacy policy and policies page have no scraping clause. First-party WordPress weather box (Base 6,050\' / Lakeview 7,400\' x 24/48/72h/1-week); OpenSnow powers only the separate forecast tab. Wayback 2026-01-08: Lakeview 24h 4" (48h 6, 1-week 26).',
        notes='Lakeview (7,400 ft) 24 Hours.',
    ),
    ResortSpec(
        resort_id="bogus-basin",
        station_id="bogus-basin:US:RESORT",
        country="US",
        tz="America/Boise",
        url="https://bogusbasin.org/your-mountain/conditions-webcams/",
        parse=_cell('<dt class="is-style-pre-header">24H</dt>\\s*<dd>\\s*<span class="value">([^<]{0,40})</span>'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'robots blocks only events/wp-admin. No terms of use (404); /restrictions-policies and privacy policy have no scraping clause. First-party \'Snow Totals\' block (12H/24H/48H/7-day/season, \'Last update … 5:57 AM\'); snowforecast.com credited only for the forecast. Wayback 2026-01-05: 24H 3" (12H 1, 48H 6, 7-day 6).',
        notes='24H.',
    ),
    ResortSpec(
        resort_id="mission-ridge",
        station_id="mission-ridge:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.missionridge.com/mountain-report/",
        parse=_cell('<td class="title">24 hr snow</td>\\s*<td class="summit">([^<]{0,40})</td>'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty, Crawl-delay 10. No terms of use (404); privacy and mountain policies have no scraping clause. First-party conditions table (Summit/Midway/Base: overnight/24/48 hr, ~5:40-6:00 AM), NWAC-partnered instruments. Wayback 2026-02-08: summit 24h 2.5" (overnight 0.5, 48h 2.5).',
        notes="Summit column; 'Trace' reads as 0.",
    ),
    ResortSpec(
        resort_id="49-degrees-north",
        station_id="49-degrees-north:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.ski49n.com/mountain-info/expanded-conditions",
        parse=_cell('<h3>Summit</h3>\\s*</div>\\s*</div>\\s*<div class="col-md-3 col-lg-2">\\s*<div class="box">\\s*<h4>12 Hours</h4>\\s*<h3>[^<]*</h3>\\s*</div>\\s*</div>\\s*<div class="col-md-3 col-lg-2">\\s*<div class="box">\\s*<h4>24 Hours</h4>\\s*<h3>([^<]{0,40})</h3>'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'robots allows all. Site /terms has no scraping/automated-access ban (only no-damage/overburden and \'not obtain … through any means not intentionally made available\' — a public page fetched twice daily). First-party conditions grid (Base/Summit x 12/24/48/72h, \'Last updated … 5:51am\'). Wayback 2026-03-06: summit 24h 1" (12h Trace, 48h 1, 72h 1).',
        notes="Summit 24 Hours; 'Trace' reads as 0.",
    ),
    ResortSpec(
        resort_id="mt-spokane",
        station_id="mt-spokane:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://www.mtspokane.com/conditions",
        parse=_cell('New Snow in Last 24 Hours</td>\\s*<td class="cell"[^>]*>([^<]{0,40})</td>'),
        unit="in",
        report_hour_local=5,
        enabled=True,
        verified="2026-10-20: " + 'robots allows all. No terms of use (404); /policies has no scraping clause. Hand-edited first-party conditions table posted 5am daily. Wayback 2026-02-25: 24h 4" (48h 4).',
        notes='\'4"+\' reads as 4; \'N/A\' -> no report.',
    ),
    ResortSpec(
        resort_id="red-mountain",
        station_id="red-mountain:CA:RESORT",
        country="CA",
        tz="America/Vancouver",
        url="https://api.redresort.com/report/full",
        parse=_red_mountain_parse,
        report_time=_red_mountain_time,
        unit="cm",
        report_hour_local=8,
        enabled=True,
        verified="2026-10-20: www.redresort.com robots disallows only wpforms uploads; api.redresort.com has no "
        "robots.txt (404). Terms and Conditions contain no scraping/automated-access/redistribution clause (lawful "
        "use, no interference only). The resort's own report API (staff-entered MountainReport) behind /report/. "
        "Wayback 2026-02-16: Metric24hours 1 cm (48h 10, 7d 10).",
        notes="Freshness-gated on MountainReport.DateTime (stale reports never count); SummerMode -> no report.",
    ),
    ResortSpec(
        resort_id="revelstoke",
        station_id="revelstoke:CA:RESORT",
        country="CA",
        tz="America/Vancouver",
        url="https://example.invalid/revelstoke",
        parse=_not_scraped,
        notes="2026-10-20 held: clean first-party block, no terms, but the 24h value is rolling (block has a 'Last Hour' field) with no report timestamp — can't attribute it to a day.",
    ),
    ResortSpec(
        resort_id="panorama",
        station_id="panorama:CA:RESORT",
        country="CA",
        tz="America/Edmonton",
        url="https://example.invalid/panorama",
        parse=_not_scraped,
        notes="2026-10-20 held: parser ready, but late posts (10:09 MST) can miss the scrape and the page needs a freshness gate on 'Updated at'.",
    ),
    ResortSpec(
        resort_id="monarch",
        station_id="monarch:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://example.invalid/monarch",
        parse=_not_scraped,
        notes="2026-10-20 held: parser ready, but captures show stale reports (Mar 29 report still up in April) — needs a 'Last Updated' freshness gate.",
    ),
    ResortSpec(
        resort_id="big-white",
        station_id="big-white:CA:RESORT",
        country="CA",
        tz="America/Vancouver",
        url="https://example.invalid/big-white",
        parse=_not_scraped,
        notes="2026-10-20 held: first-party, no website terms, but a Trademark Policy prohibits use of its 'intellectual property' without consent — ask.",
    ),
    ResortSpec(
        resort_id="sun-peaks",
        station_id="sun-peaks:CA:RESORT",
        country="CA",
        tz="America/Vancouver",
        url="https://example.invalid/sun-peaks",
        parse=_not_scraped,
        notes='2026-10-20 held: terms require prior written consent for commercial use of content — outreach.',
    ),
    ResortSpec(
        resort_id="fernie",
        station_id="fernie:CA:RESORT",
        country="CA",
        tz="America/Edmonton",
        url="https://example.invalid/fernie",
        parse=_not_scraped,
        notes='2026-10-20 held: RCR terms (Fernie, Kicking Horse, Kimberley, Nakiska) forbid redistribution without written permission — outreach.',
    ),
    ResortSpec(
        resort_id="smugglers-notch",
        station_id="smugglers-notch:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/smugglers-notch",
        parse=_not_scraped,
        notes="2026-10-20 held: 'New Snowfall' is overnight, not 24h.",
    ),
    ResortSpec(
        resort_id="whiteface",
        station_id="whiteface:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/whiteface",
        parse=_not_scraped,
        notes='2026-10-20 held: Cloudflare challenge on every request (getting past it = circumvention).',
    ),
    ResortSpec(
        resort_id="sun-valley",
        station_id="sun-valley:US:RESORT",
        country="US",
        tz="America/Boise",
        url="https://example.invalid/sun-valley",
        parse=_not_scraped,
        notes='2026-10-20 held: privacy policy binds visitors to an unpublished Terms of Service (also Snowbasin) — ask Earl Holding.',
    ),
    ResortSpec(
        resort_id="taos",
        station_id="taos:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://example.invalid/taos",
        parse=_not_scraped,
        notes='2026-10-20 rejected: OpenSnow-fed widget and terms ban bots (also Aspen Snowmass: personal-use terms; Brian Head/MCP: robots banned; Schweitzer: Alterra; Mt Washington BC: OpenSnow).',
    ),
    ResortSpec(
        resort_id="white-pass",
        station_id="white-pass:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://skiwhitepass.com/fragments/blocks/074101ba-e952-45eb-9a87-d90143a17849/shared_component/0",
        parse=_cell('<h5>New Snow 24 Hours</h5>\\s*<p>([^<]{0,40})</p>'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty (page and fragment). No terms of use (404); privacy policy has no scraping clause. First-party patrol snow report; NWAC supplies only temps, NWS only the forecast. Since the 2026 redesign the report is a same-site HTML fragment with the same markup. Wayback 2025-12-17: 24h 5" (12h 5, 36h 5); 2026-02-17: 1 (12h 1, 36h 2); 2026-02-24: 6.',
        notes='Fragment UUID found via data-fragment-type="snow_report" on the snow-report page — re-discover if it 404s. Stamp has no year, so no freshness gate.',
    ),
    ResortSpec(
        resort_id="bluewood",
        station_id="bluewood:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://bluewood.com/conditions",
        parse=_cell('CONDITIONS: SUMMIT</strong>.{0,3000}?dvmd_tm_col_1 dvmd_tm_row_1 [^>]*><div class="dvmd_tm_cdata">([^<]{0,40})</div>'),
        report_time=_stamp('Last updated</p></div>\\s*</div><div[^>]*>\\s*<div class="et_pb_text_inner">([^<]{0,60})</div>', '%B %d, %Y (%I:%M %p)', 'America/Los_Angeles'),
        unit="in",
        report_hour_local=5,
        enabled=True,
        verified="2026-10-20: " + 'robots disallows only wp-admin/WooCommerce (Crawl-delay 10). No terms of use (404); privacy policy has no scraping clause. First-party hand-entered Divi tables (Base/Summit x 24h/48h/depth). Wayback 2025-12-29: summit 24h 5" (48h 8, base 4); 2026-02-01: 0.',
        notes='Summit; freshness-gated (captures showed reports up to 2 weeks old).',
    ),
    ResortSpec(
        resort_id="discovery-basin",
        station_id="discovery-basin:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://www.skidiscovery.com/snow-report/",
        parse=_cell('<p>New Snow, 24 hours</p>\\s*<p class="little-text inch">([^<]{0,40})</p>'),
        report_time=_stamp('<span class="tablinks">Updated: ([^<]+)</span>', '%B %d, %Y at %I:%M %p', 'America/Denver'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'robots.txt only Crawl-delay 30. No terms of use (404); no legal links in footer. First-party snow-report tiles (24/48/72h/YTD), \'Updated: <Month D, YYYY> at <h:mm am>\'. Wayback 2025-12-09: 24h 2" (48h 5, 72h 11).',
        notes='Discovery Basin; freshness-gated.',
    ),
    ResortSpec(
        resort_id="showdown",
        station_id="showdown:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://www.showdownmontana.com/snow-report",
        parse=_cell('<h2[^>]*>([^<]{0,40})</h2>\\s*<p[^>]*>\\s*<span[^>]*>\\s*Last 24 Hour\\s*</span>'),
        report_time=_stamp('Last Update(?:<[^>]+>|\\s|:)*(\\d{1,2}/\\d{1,2}/\\d{4})', '%m/%d/%Y', 'America/Denver'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'Squarespace default robots allows the page. Terms & Conditions (TermsFeed template) have no scraping, automated-access or reproduction clause. First-party hand-edited report (\'Last Update: MM/DD/YYYY\'). Wayback 2025-11-28: 24h 5" (overnight 4); 2025-12-18: 0; live 10/11/2026: 19 (overnight 15).',
        notes="Date-only stamp (00:00 local): yesterday's report is stale by the 20:30 UTC scrape.",
    ),
    ResortSpec(
        resort_id="blacktail",
        station_id="blacktail:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://blacktailmountain.com/snow-report/",
        parse=_cell('<strong>New Snow 24 Hours: &nbsp;</strong>([^<]{0,40})</li>'),
        report_time=_stamp('<h3>Reported: ([^<]+)</h3>', '%I:%M %p, %a, %m/%d/%y', 'America/Denver'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty. No terms of use or privacy page (404). First-party staff-entered report (\'Reported: h:mm am, Day, MM/DD/YY\'). Wayback 2026-03-16: 24h 6" (overnight 2, summit depth 49).',
        notes='Blank fields read as no report; freshness-gated.',
    ),
    ResortSpec(
        resort_id="montana-snowbowl",
        station_id="montana-snowbowl:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://www.montanasnowbowl.com/report.php",
        parse=_cell('SIZE=2>Summit</TD>\\s*<TD[^>]*>[^<]*</TD>\\s*<TD[^>]*>([^<]{0,40})</TD>'),
        report_time=_stamp('<b>Updated:</b>\\s*([^<]+?)\\s*</center>', '%A %b %d, %Y %I:%M %p', 'America/Denver'),
        unit="in",
        report_hour_local=5,
        enabled=True,
        verified="2026-10-20: " + 'robots blocks only WP dirs and /index.php (report.php allowed). No terms of use (404); safety-and-policies page has no scraping clause. First-party hand-maintained report table with a 7-day daily-snowfall history. Wayback 2026-02-25: Summit 7" (Base 4, history Day-1 7).',
        notes="'New Snow' is the daily amount of the ~4:45 am report (confirmed by its 7-day history); freshness-gated.",
    ),
    ResortSpec(
        resort_id="red-river",
        station_id="red-river:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://www.redriverskiarea.com/winter/winter-mountain/snow-report/",
        parse=_cell('<dl class="SnowReport-measure"><dt>Last 24 Hours</dt><dd>([^<]{0,40})</dd></dl>'),
        report_time=_stamp('<dt>Updated</dt>\\s*<dd>([^<]+)</dd>', '%I:%M %p, %B %d, %Y', 'America/Denver'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + "robots 'Allow: /'. No terms of use (404); 'Policies' page has no scraping clause. First-party SnowReport block (same platform as Jay Peak/Bolton Valley); OpenSnow powers only the forecast. Wayback 2025-12-31 / 2026-01-24: 24h 0 (72h 0, 5d 0).",
        notes="Freshness-gated on 'Updated' (captures 1-2 days old).",
    ),
    ResortSpec(
        resort_id="tamarack",
        station_id="tamarack:US:RESORT",
        country="US",
        tz="America/Boise",
        url="https://example.invalid/tamarack",
        parse=_not_scraped,
        notes="2026-10-20 held: parser ready (patrol report, 'Updated MM/DD/YYYY at 7:00 AM'), but its privacy policy binds visitors to unpublished 'Terms and Conditions of Use' (as Sun Valley) — ask.",
    ),
    ResortSpec(
        resort_id="maverick",
        station_id="maverick:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://example.invalid/maverick",
        parse=_not_scraped,
        notes='2026-10-20 held: free-typed Square Online text, sporadic updates (8-day-old report seen), no non-zero winter capture.',
    ),
    ResortSpec(
        resort_id="great-divide",
        station_id="great-divide:US:RESORT",
        country="US",
        tz="America/Denver",
        url="https://example.invalid/great-divide",
        parse=_not_scraped,
        notes="2026-10-20 rejected: terms ban access 'by a bot, script'; but it has a clean JSON API with history — outreach.",
    ),
    ResortSpec(
        resort_id="mt-ashland",
        station_id="mt-ashland:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url="https://example.invalid/mt-ashland",
        parse=_not_scraped,
        notes='2026-10-20 rejected by terms (data mining/harvesting/extracting) — also China Peak, Homewood, Mt Baldy, Ski Santa Fe, Ski Apache, Sunrise Park, Bear Valley, Powder Mountain; MCP resorts (Purgatory, Nordic Valley, Willamette Pass, Lee Canyon, Sipapu, Pajarito, Arizona Snowbowl); OpenSnow-fed: Eagle Point, Sundance, Angel Fire, Hurricane Ridge.',
    ),
)

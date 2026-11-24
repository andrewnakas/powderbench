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


_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"  # ".5" (typed without the 0) is 0.5, not 5


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


def _stamp(pattern: str, fmt: str | list[str], tz: str):
    """report_time from a page's own 'Updated …' text: regex group 1, parsed
    with `fmt` (one strptime format, a list tried in order, or "iso") in the
    resort's zone; a.m./p.m. and case are normalised."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def parse(body: str) -> str | None:
        m = re.search(pattern, body, re.S)
        if not m:
            return None
        text = re.sub(r"\s+", " ", m.group(1).replace("a.m.", "AM").replace("p.m.", "PM")).strip()
        text = re.sub(r"\b(am|pm)\b", lambda x: x.group(1).upper(), text)
        for f in [fmt] if isinstance(fmt, str) else fmt:
            try:
                if f == "iso":
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                    return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))).isoformat()
                return datetime.strptime(text, f).replace(tzinfo=ZoneInfo(tz)).isoformat()
            except ValueError:
                continue
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
        enabled=False,
        verified=(
            "2026-07-10: robots.txt allows all; /es/legal reviewed — no "
            "scraping clause; report is server-rendered on their own page "
            "(no aggregator feed detected)"
        ),
        notes="Mountain-wide 24h value ('0cm / 0in' format); page updates intraday ~14:00 CLT, so the 20:30 UTC scrape is the fresh capture. "
        "2026-10-20 re-check: owner Mountain Capital Partners bans robots on brianhead.com, but vallenevado.com's own "
        "/es/legal/ page and 2026 Terminos y Condiciones PDF contain no robots/scraping/reproduction clause. "
        "2026-11-19 DISABLED (conservative): MCP's Website Terms of Use (Last Updated 2024-12-30, served on every "
        "MCP resort site, e.g. purgatory.ski/website-terms-of-use/) define the 'Sites' as mcp.ski 'each of our MCP "
        "Resort websites', with MCP Resorts 'in Chile', and ban 'any robot, spider or other automatic device, process "
        "or means to access the Sites for any purpose, including monitoring or copying any of the material'. MCP has "
        "been Valle Nevado's majority owner since 2023. History kept; re-enable only with MCP's permission (OUTREACH.md).",
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
        enabled=False,  # see notes
        verified="2026-10-20: " + 'robots \'Disallow:\' empty. No terms of use (404); privacy policy and policies page have no scraping clause. First-party WordPress weather box (Base 6,050\' / Lakeview 7,400\' x 24/48/72h/1-week); OpenSnow powers only the separate forecast tab. Wayback 2026-01-08: Lakeview 24h 4" (48h 6, 1-week 26).',
        notes="Lakeview (7,400 ft) 24 Hours. 2026-10-23 disabled: every scheduled scrape from GitHub's runners "
        "returns an HTTP error while the page loads normally elsewhere (the site blocks cloud IPs); not worked around.",
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
        url='https://www.panoramaresort.com/panorama-today/daily-snow-report',
        parse=_cell('<h4 class="margin-bottom-0">([^<]{0,40})<sup class="light-text">cm</sup></h4>\\s*<p class="xsmall text-uppercase lspace">24 Hours</p>'),
        report_time=_stamp('Updated at (\\d{1,2}:\\d{2} [AP]M \\d{1,2} [A-Z][a-z]+, \\d{4})', '%I:%M %p %d %B, %Y', 'America/Edmonton'),
        unit="cm",
        report_hour_local=10,
        enabled=True,
        verified="2026-11-19: robots.txt (www.panoramaresort.com) is 'User-agent: * Allow: /' - page allowed for PowderBench; page and endpoint are the same host. No terms of use page (/terms and variants 404); the privacy policy has no scraping, automated-access or reproduction clause. Provenance: Panorama's own server-rendered Silverstripe page ('Official snow report & weather report for Panorama Resort'), no OpenSnow/OnTheSnow/'Powered by' credit on the snow block. Wayback 2026-03-01 (id_): 24 Hours 1 cm (Overnight 0, 48 Hours 5, 7 Days 22, season 451, 'Updated at 9:10 AM 1 March, 2026'); other winter captures 2026-01-20, 2026-02-10, 2026-01-01, 2026-03-28 all read 24h 0 with consistent 48h/7d and stamps 6:49-10:09 AM. Live page fetched once: same markup, values blank off-season (parser returns None), stamp 'Updated at 2:17 PM 2 November, 2026' parses.",
        notes="Re-check of the 2026-10-20 hold ('late posts (10:09 MST) can miss the scrape'). With report_hour_local=10 the 16:30 UTC scrape (09:30 MST) attributes to D-2 and loses to the previous day's 20:30 UTC (13:30 MST) scrape, so every day is taken from the afternoon scrape, which is after all observed posts (6:49-10:09). Stamp is the 'Updated at' heading on the lifts/trails block; on the winter captures its date matched the snow block's report date. Freshness gate caveat: a report posted at ~10 AM is only ~27 h old at the next day's 13:30 scrape, so one missed post can pass the 30 h gate (STUCK_DAYS still catches non-zero repeats). Station elevation is the resort summit figure; the snow-plot location is not stated on the page.",
    ),
    ResortSpec(
        resort_id="monarch",
        station_id="monarch:US:RESORT",
        country="US",
        tz="America/Denver",
        url='https://skimonarch.com/conditions/',
        parse=_cell('class="accumulation__table">\\s*<thead>\\s*<th>[^<]{0,120}</th>\\s*<th>([^<]{0,120})</th>.{0,3000}?</thead>\\s*<tbody>\\s*<tr>\\s*<td>\\s*Overnight\\s*</td>\\s*<td>\\s*24 hours\\s*</td>'),
        report_time=_stamp('Last Updated: ([^<]{0,60})</p>', '%B %d, %Y - %I:%M %p', 'America/Denver'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified='2026-11-19: robots.txt (skimonarch.com) disallows only /cgi-bin/, /tmp/, /junk/ - /conditions/ allowed for PowderBench. No website terms of use: /terms redirects to /terms-conditions/, which covers passes, tickets, refunds and cat skiing only (no scraping, automated-access or reproduction clause); /terms-of-use, /privacy-policy, /legal 404; cookie policy has no such clause. Value = Monarch\'s own server-rendered \'accumulation__table\' on its /conditions/ WordPress subsite with \'Last Updated: <Month D, YYYY - hh:mm AM>\'; OpenSnow powers only the separate WEATHER FORECAST block. Wayback 2025-01-06: 24h 7" (overnight 1, 48h 12, 7d 27); 2025-12-04: 5 (48h 7, 7d 14).',
        notes="Was held 2026-10-20 for stale reports (a Mar 29 report still up in April); now freshness-gated on 'Last Updated'. Values sit in a <thead> row and labels in the <tbody> row below, so the pattern takes the 2nd <th> and asserts the label row reads Overnight, 24 hours. Cells are padded with ~45 chars of whitespace (hence {0,120}). Morning report posts ~05:15-05:45 MT.",
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
    ResortSpec(
        resort_id="magic-mountain",
        station_id="magic-mountain:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://www.magicmtn.com/snow-report",
        parse=_cell('"SnowReport":\\{"[0-9a-f-]{36}":\\{.{0,6000}?"24HrTotal":"([^"]{0,40})"'),
        report_time=_stamp('"SnowReport":\\{"[0-9a-f-]{36}":\\{.{0,6000}?"_updatedDate":\\{"\\$date":"([^"]+)"', ['iso'], 'America/New_York'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'Wix robots Allow: /. No terms of use published (all terms/legal paths 404, no footer link). Values are the resort\'s own Wix CMS SnowReport record (24HrTotal/48HrTotal/week/season) with a dated patrol write-up — the visible first block is a stale repeater, so the CMS record is read. Wayback 2026-03-06: 24h 2" (48h 2, week 6-8); 2026-02-05: 0.',
        notes="Freshness-gated on the record's _updatedDate.",
    ),
    ResortSpec(
        resort_id="ragged",
        station_id="ragged:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://www.raggedmountainresort.com/mountain-report-cams/",
        parse=_cell('<h6>Last 24 hrs\\.</h6>\\s*<h3>([^<]{0,40})</h3>'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty (Crawl-delay 10). No terms of use (404); privacy and policies pages have no scraping clause; Pacific Group Resorts \'pgri\' theme (as Jay Peak), parent publishes no terms. First-party Current Conditions block. Wayback 2026-01-20: 24h 4" (overnight 1, 48h 8); 2026-03-14: 2/2/2.',
        notes='No report timestamp on the page.',
    ),
    ResortSpec(
        resort_id="saddleback",
        station_id="saddleback:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://saddlebackapi.netlify.app/.netlify/functions/getAll",
        parse=_cell('"snow":\\{"id":"[^"]*".{0,800}?"freshSnowFallDepth24H":\\{"value":([^,}]{0,10}),"unit":"INCH"\\}'),
        report_time=_stamp('"snow":\\{"id":"[^"]*".{0,2000}?"lastModified":"([^"]+)"', ['iso'], 'America/New_York'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified="2026-10-20: " + 'saddlebackmaine.com robots \'Disallow:\' empty; API host has no robots.txt (404). Terms of Use cover only lift-pass fraud and SMS. Endpoint = the resort\'s own Netlify function behind its mountain-report page (resort-entered Lumiplan-style feed, as Mt Hood Meadows). Wayback 2026-02-13: 24h 2" (48h 8, 7d 11, lastModified 2026-02-12 06:15).',
        notes="Never use the 'overnight' field (mirrors base depth). Freshness-gated on lastModified.",
    ),
    ResortSpec(
        resort_id="wachusett",
        station_id="wachusett:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://www.wachusett.com/the-mountain/your-visit/snow-report/",
        parse=_cell('<span id="snowfallLabel" class="main-header-wrap__snowreport__snow" aria-label="24h Snowfall">([^<]{0,40})</span>'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified="2026-10-20: " + 'robots (Yoast) blocks only search URLs. No website terms of use (/termsandconditions covers passes/tickets only); privacy policy has no scraping clause. 24h Snowfall is server-rendered from the resort\'s own wp-api SnowReport. Wayback 2026-01-01: 2"; 2026-01-19: 6"; 2026-02-12: 2.5" (each agrees with that day\'s narrative).',
        notes='Stamp is weekday-only, so no freshness gate.',
    ),
    ResortSpec(
        resort_id="holiday-valley",
        station_id="holiday-valley:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://www.holidayvalley.com/snow-report/",
        parse=_cell('<span>PAST</span>\\s*<span>24HRS</span></h3>\\s*<span class="big-number">\\s*([^<]{0,40})</span>'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty. No terms of use (404); /policies has no scraping/reuse clause. First-party snow_report module. Wayback 2025-12-11: 1"; 2026-02-06: 3"; 2026-02-27: 3" (\'We received 3 inches\').',
        notes='No report timestamp; several consistent captures.',
    ),
    ResortSpec(
        resort_id="elk-mountain",
        station_id="elk-mountain:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://www.elkskier.com/conditions/",
        parse=_cell('<div class="table_column">Natural snow last 24 hours</div>\\s*<div class="table_column">([^<]{0,40})</div>'),
        report_time=_stamp('<div class="table_column">Current Date</div>\\s*<div class="table_column">(\\d{2}/\\d{2}/\\d{4})', ['%m/%d/%Y'], 'America/New_York'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'robots: Yoast \'Disallow:\' empty. No terms of use (404); privacy policy has no scraping clause. First-party Conditions Report table with a \'Current Date\' stamp. Wayback 2025-12-11: \'4" - 6"\' -> 5 (stamp 12/11/2025); 2026-01-03: 1".',
        notes="Freshness gate REQUIRED: a Dec 2025 capture still showed last season's 03/22/2025 report.",
    ),
    ResortSpec(
        resort_id="timberline-wv",
        station_id="timberline-wv:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://timberlinemountain.com/snow-report/",
        parse=_cell('<p class="resort-info-title">Snow Last 24 Hours</p>\\s*<p>([^<]{0,40})</p>'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified="2026-10-20: " + "robots disallows only /wp-admin/. /terms-conditions covers lift tickets/facilities only. First-party report from the site's own snow-report plugin. Wayback 2025-12-03: 24h 8 (7d 8); 2026-01-16: 3 (7d 3).",
        notes='No report timestamp.',
    ),
    ResortSpec(
        resort_id="wintergreen",
        station_id="wintergreen:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://www.wintergreenresort.com/mountain-report-cams/",
        parse=_cell('<h6>Last 24 hrs\\.</h6>\\s*<h3>([^<]{0,40})</h3>'),
        report_time=_stamp('<p class="card-text">\\s*Updated\\s+([A-Z][a-z]+ \\d{1,2}, \\d{4} \\d{1,2}:\\d{2} [AP]M)', ['%B %d, %Y %I:%M %p'], 'America/New_York'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified="2026-10-20: " + 'robots disallows only wpforms uploads and /cdn-cgi/ (Crawl-delay 10). No terms of use (404); privacy policy has no scraping clause. Pacific Group Resorts (as Jay Peak), not Vail. First-party Current Conditions block. Wayback 2025-01-21: 24h 2" (overnight 0, 48h 2); 2026-02-05: 0 (48h 0.5, \'Updated February 5, 2026 8:02 AM\').',
        notes="Freshness-gated when the 'Updated' line is present (from Jan 2026).",
    ),
    ResortSpec(
        resort_id="crystal-mountain-mi",
        station_id="crystal-mountain-mi:US:RESORT",
        country="US",
        tz="America/Detroit",
        url="https://www.crystalmountain.com/ski/cams-conditions/mountain-report",
        parse=_cell('<h3 class="h5">\\s*24h Snowfall\\s*</h3>\\s*<div class="f-heading f-w-300 lh-1 values">\\s*<span class="value">([^<]{0,40})</span>'),
        report_time=_stamp('<h2>Report for ([A-Z][a-z]+ \\d{1,2}, \\d{4})</h2>', ['%B %d, %Y'], 'America/Detroit'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'robots Drupal default. No website terms of use (/terms-conditions covers tickets/liability only). First-party snow-report block headed \'Report for <date>\'. Wayback 2026-01-01: 24h 4" (Report for December 31, 2025); 2026-02-02: 2".',
        notes='Date-only stamp (00:00 local).',
    ),
    ResortSpec(
        resort_id="caberfae",
        station_id="caberfae:US:RESORT",
        country="US",
        tz="America/Detroit",
        url="https://caberfaepeaks.com/snow-report/",
        parse=_cell("<td class=''>24 HOUR SNOWFALL:([^<]{0,40})</td>"),
        report_time=_stamp("Today(?:&#8217;|’|')s Date:\\s*(\\d{1,2}/\\d{1,2}/\\d{2,4})", ['%m/%d/%Y', '%m/%d/%y'], 'America/Detroit'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + 'robots \'Disallow:\' empty. No terms of use (footer has camping/cancellation policies only). First-party Daily Conditions table (24/48/72h, YTD) with \'Today\'s Date\'. Wayback 2026-01-23: 24h 8" (48h 11, 72h 17; comment \'received 8″ of new snow\').',
        notes='Date-only stamp.',
    ),
    ResortSpec(
        resort_id="nubs-nob",
        station_id="nubs-nob:US:RESORT",
        country="US",
        tz="America/Detroit",
        url="https://www.nubsnob.com/conditions-tables/",
        parse=_cell("New Snow since yesterday:</div><div class='conditions-cell cell-1'>([^<]{0,40})</div>"),
        report_time=_stamp("<div class='conditions-cell cell-0'>Date:</div><div class='conditions-cell cell-1'>\\s*(\\d{1,2}/\\d{1,2}/\\d{2,4})", ['%m/%d/%y', '%m/%d/%Y'], 'America/Detroit'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified="2026-10-20: " + 'robots allows the page (Crawl-delay 20). No website terms of use (only a liability release). First-party conditions table; \'New Snow since yesterday\' is the daily report-to-report amount. Wayback 2026-01-15: 1" (3d 1, 7d 7, Date 1/14/26).',
        notes='Date-only stamp.',
    ),
    ResortSpec(
        resort_id="cascade-mountain",
        station_id="cascade-mountain:US:RESORT",
        country="US",
        tz="America/Chicago",
        url="https://www.cascademountain.com/snow-report/",
        parse=_cell('<p>New Snow 24 hrs</p>\\s*<h5>([^<]{0,40})</h5>'),
        report_time=_stamp('Last Update:\\s*(\\d{1,2}/\\d{1,2}/\\d{2,4})', ['%m/%d/%y', '%m/%d/%Y'], 'America/Chicago'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified="2026-10-20: " + "robots allows the page. No website terms of use (/terms-conditions covers gift cards/tickets). First-party Elementor page ('New Snow 24 hrs') with a staff 'Last Update' note. Wayback 2026-02-17: 0 (Last Update 2/16/26 6:40 a.m.); 2025-12-03: 10 but stamped 12/1 -> stale.",
        notes='Freshness gate REQUIRED; some captures hit a JS interstitial (expect occasional failed scrapes).',
    ),
    ResortSpec(
        resort_id="burke",
        station_id="burke:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/burke",
        parse=_not_scraped,
        notes="2026-10-20 held: first-party table, no terms, but hand-typed entries are inconsistent (24h 3 vs 48h 1) and there's no timestamp.",
    ),
    ResortSpec(
        resort_id="black-mountain-nh",
        station_id="black-mountain-nh:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/black-mountain-nh",
        parse=_not_scraped,
        notes='2026-10-20 held: no 48h/7d to cross-check and no reliable timestamp.',
    ),
    ResortSpec(
        resort_id="snow-ridge",
        station_id="snow-ridge:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/snow-ridge",
        parse=_not_scraped,
        notes="2026-10-20 held: no timestamp, and a capture's 24h (10) disagrees with its own narrative (8).",
    ),
    ResortSpec(
        resort_id="tenney",
        station_id="tenney:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/tenney",
        parse=_not_scraped,
        notes='2026-10-20 held: only one winter capture (value 0); recheck in season (propapps platform).',
    ),
    ResortSpec(
        resort_id="hatley-pointe",
        station_id="hatley-pointe:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/hatley-pointe",
        parse=_not_scraped,
        notes='2026-10-20 held: only one winter capture (value 0); recheck in season.',
    ),
    ResortSpec(
        resort_id="berkshire-east",
        station_id="berkshire-east:US:RESORT",
        country="US",
        tz="America/New_York",
        url="https://example.invalid/berkshire-east",
        parse=_not_scraped,
        notes='2026-10-20 held: terms forbid reproduction without written consent (also Catamount) — outreach. Rejected by terms: Cranmore, Jiminy Peak, Windham, Greek Peak, Plattekill, Massanutten, Whitecap; Lost Valley (SnoCountry-fed); ORDA (Gore, Belleayre) behind Cloudflare.',
    ),
    ResortSpec(
        resort_id="sundance",
        station_id="sundance:US:RESORT",
        country="US",
        tz="America/Denver",
        url='https://www.sundanceresort.com/mountain-report/',
        parse=_cell('data-lookup="snow-weather.24hr_snow"[^>]*>([^<]{0,40})</span>'),
        unit="in",
        report_hour_local=5,
        enabled=True,
        verified='2026-11-19: robots.txt \'User-agent: *\' allows / and disallows only /admin/, /login/, /account/, /cart/, /checkout/, /search/ (page and /api/ allowed). No terms of use (/terms-of-use, /terms-and-conditions 404); the footer links only a privacy policy, which has no scraping or automated-access clause. Value = Sundance\'s own staff-entered \'snow-weather\' rows (id/label/status_value/display/notes, the same sheet as its lift-status/grooming/roads rows), server-rendered into data-lookup spans and also served by the same-host /api/mountain-information; OpenSnow supplies only the separate \'forecast\' object (weather, icons). Wayback 2026-02-18: 24hr 1" (48hr 1, base 15, YTD 41).',
        notes="No report timestamp on the page ('Snow totals are updated daily at 5 AM'); the 'Updated ... MST' label belongs to the OpenSnow weather block, not the snow totals. The markup before winter 2025-26 was different (no data-lookup), so re-check if the page is redesigned. Alternative cleaner source: JSON https://www.sundanceresort.com/api/mountain-information, row id '24hr_snow' in 'snow-weather' (not Wayback-verified).",
    ),
    ResortSpec(
        resort_id="lost-trail",
        station_id="lost-trail:US:RESORT",
        country="US",
        tz="America/Denver",
        url='https://losttrail.com/snow-report/',
        parse=_cell('New Snow, 24 hours</div>\\s*</div>\\s*</div>\\s*<div class="elementor-element[^>]*>\\s*<div class="elementor-widget-container">\\s*<span class="elementor-heading-title[^"]*">([^<]{0,40})</span>'),
        report_time=_stamp('Snow Report for ([^<]{0,60})</div>', '%B %d, %Y %I:%M %p', 'America/Denver'),
        unit="in",
        report_hour_local=6,
        enabled=True,
        verified='2026-11-19: robots.txt (losttrail.com) disallows only /wp-admin/ - /snow-report/ allowed for PowderBench. No terms of use (/terms-of-use redirects to the homepage); footer \'Lost Trail Policies\' (skier policies) and privacy policy have no scraping, automated-access or redistribution clause. Value = Lost Trail\'s own staff-written Elementor report (\'Snow Report for <Month D, YYYY h:mm am>\', narrative + New Snow since lifts closed/24h/48h/week tiles); no aggregator feed or credit on the page. Wayback 2025-12-01: 24h 3" (48h 3, week 7); 2025-01-21: 2 (48h 3, week 5).',
        notes="Open Thu-Sun + holidays, so reports are only posted on operating days (freshness gate drops the rest). Most reports post 05:00-09:00 MT, but some are stamped late afternoon (e.g. 'January 19, 2025 5:00 pm'). Uses the 24-hour tile, not 'since lifts closed'. Off-season the March 30 closing report stays up, and the freshness gate keeps it out.",
    ),
    ResortSpec(
        resort_id="lookout-pass",
        station_id="lookout-pass:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url='https://skilookout.com/snow-report/',
        parse=_cell('<h4>Summit</h4>\\s*<div class="snow-data">\\s*<div class="data-row">\\s*<span class="label">24h Snow:</span>\\s*<span class="value">([^<]{0,40})</span>'),
        report_time=_stamp('<div class="powderwatch-timestamp">\\s*Last updated: ([^<]+?), P[SD]T\\s*</div>', '%B %d, %Y at %I:%M %p', 'America/Los_Angeles'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified='2026-11-19: robots.txt on skilookout.com (page and the same-host JSON) disallows only /wp-admin/; PowderBench allowed. No terms of use published (/terms-of-use/ is a 404, no footer link); the privacy policy, SMS terms and \'Mountain Policies\' page have no scraping, automated-access or redistribution clause. The numbers are the resort\'s own PowderWatch WordPress form (saved from /wp-admin/admin.php?page=powderwatch and mirrored to /wp-content/uploads/powderwatch/conditions.json), server-rendered into the snow-report page; no aggregator involved (NOAA is only linked). Wayback 2025-12-02: summit 24h 6" (patrol notes \'6″ summit … as of 7:30 am\'); 2025-12-25: 0 (48h .5, 72h 1); 2026-01-17 and 2026-02-28: 0; live 2026-11-18: 2 (48h 5, 72h 10).',
        notes="Summit block (Base block follows). MUST FIX BEFORE ENABLING: Lookout types leading-dot decimals ('.5″', 48h on 2025-12-25) and registry `_reading` reads '.5&#8243;' as 5.0 (checked against the repo's code); normalise a leading '.' to '0.' in `_reading` (e.g. re.sub(r'(?<![\\d.])\\.(\\d)', r'0.\\1', text)) or use a custom parser. Stamp: current display 'Last updated: November 18, 2026 at 8:02 AM, PST'. In Dec 2025 it had no zone suffix and showed UTC (3:50 PM for a 7:50 am report), so the pattern requires ', PST/PDT' and gives no stamp for that format. The form is also re-saved in the late afternoon (4-6 PM stamps), so the 30 h gate is coarse. Alternative endpoint (same data): https://skilookout.com/wp-content/uploads/powderwatch/conditions.json -> data.snow_report.summit.24h, last_updated (local), timestamp (epoch); not checked in Wayback. A separate new_snow_since_4pm field exists, so 24h is distinct from the overnight figure.",
    ),
    ResortSpec(
        resort_id="leavenworth",
        station_id="leavenworth:US:RESORT",
        country="US",
        tz="America/Los_Angeles",
        url='https://skileavenworth.com/conditions',
        parse=_cell('views-label-field-24-hrs">24 HRS</span><div class="field-content">([^<]{0,40})</div>'),
        report_time=_stamp('views-label-changed">Reported at: </span><span class="field-content">(?:<time datetime="[^"]+">)?([^<]+)<', '%I:%M%p %m/%d/%Y', 'America/Los_Angeles'),
        unit="in",
        report_hour_local=7,
        enabled=True,
        verified='2026-11-19: robots.txt is Drupal\'s default (core/profiles/admin/search paths only); /conditions is allowed for PowderBench. No terms of use (/terms is a 404, none linked); the privacy policy has no scraping, automated-access or redistribution clause. The values are the Leavenworth Winter Sports Club\'s own Drupal \'snowfall\' view (staff-entered Overnight / 24 HRS / 7 Day / Base fields plus the node\'s \'Reported at\' time), server-rendered. The Nordic tab\'s Nordic Pulse widget and the wunderground link are separate. Wayback 2023-12-27: 24h 4.00" (overnight 4.00, 7-day 4.25, base 6, reported 8:23AM 12/26/2023); 2024-01-16: 0 (7-day 14.50); 2025-01-20 and 2026-03-23: 0; live 2026-11-01 report: 0.',
        notes="'24 HRS' is its own field, separate from 'Overnight'. Freshness-gated on 'Reported at'. The pattern reads the display text, so it works on both the 2023 plain-text markup and the 2025+ <time datetime> markup. Reports stay up for weeks or months (a March 2025 report was still showing in Dec 2025), so the gate matters. Empty fields -> None. Very low hill (~1,500-1,700 ft); it only reports when it operates and has many zero or rain days, so it adds little signal. No non-zero 24h capture of the current (2025+) markup exists, but the 24h cell markup is unchanged since 2023.",
    ),
    ResortSpec(
        resort_id="peek-n-peak",
        station_id="peek-n-peak:US:RESORT",
        country="US",
        tz="America/New_York",
        url='https://www.pknpk.com/conditions-report/',
        parse=_cell('<h4 class="[^"]*">24 Hr</h4>\\s*<div class="[^"]*">([^<]{0,40})</div>'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified='2026-11-19: robots (WordPress/WooCommerce) disallows only /cdn-cgi/, /wp-admin/, /wp-json/ and query URLs; the page is allowed for PowderBench. No website terms of use (/terms-of-use/ 404; page sitemap lists only privacy, condo and inn policies); /privacy/ has no scraping or reuse clause. Owner Scott Enterprises (Erie PA, visitscott.com, Wix) links no website terms (/terms-of-use 404). First-party Toolset (wpv-view) Conditions block on the resort\'s own WordPress (Snowfall 24 Hr / 48 Hr, Season Total); no aggregator markers. Wayback 2026-01-03: 24h 4" (48h 28, season 92); 2026-02-14: 1 (48h 2, season 158, up 2 from 156 on 02-11); 2025-02-16: 2 (48h 4); 2026-02-08 and 02-11: 0. Live page has the same structure (0 IN., season 176 carried from last season).',
        notes="Values like '4 IN.'. No report timestamp on the page (report_hour_local 8 is inferred, not stamped), so no freshness gate; off-season it keeps showing 0 and last season's total. Season total is natural snowfall (cross-checks with 48h). Station coordinates/elevation are approximate (not taken from the page); confirm before stations.yaml.",
    ),
    ResortSpec(
        resort_id="shanty-creek",
        station_id="shanty-creek:US:RESORT",
        country="US",
        tz="America/Detroit",
        url='https://www.shantycreek.com/ski/conditions-cams/',
        parse=_cell('<div class="snowfall">\\s*<h3>Snowfall</h3>\\s*<div class="items">\\s*<div class="item">\\s*<div class="title">Past 24 Hours</div>\\s*<div class="number">([^<]{0,40})</div>'),
        unit="in",
        report_hour_local=8,
        enabled=True,
        verified="2026-11-19: robots allows the page (PowderBench UA). No terms of use (/terms-of-use/ and /terms-and-conditions/ 404; page sitemap lists only drone, privacy, golf rain-check and lodging policies); privacy policy has no scraping or reuse clause. First-party Snowfall block in the resort's own WordPress theme (Past 24 Hours / Past 2 Days / Past 3 Days / Season-To-Date), no aggregator markers. Wayback 2025-01-03: 24h 4 (2d 10, 3d 10; write-up 'almost 10” of freshies since New Year's Day'); 2024-12-08: 2 (3/5) and 12-09: 0 (2/3, rolls forward); 2025-12-05: 4 (6/8, season 24) and 12-09: 2 (3/5, season 30); 2026-02-13: 2 (2/2). Live page has the same structure (0, season 198 from last season).",
        notes="Nine winter captures, all internally consistent (24h <= 2d <= 3d, season deltas match). No machine-readable timestamp: the Schuss write-up heading ('January 3, 2025') lost its year in 2025-26 ('December 3') and is sometimes absent, so no freshness gate; the numbers were still updated daily when the write-up lagged. report_hour_local 8 is inferred. Station coordinates/elevation are approximate (not taken from the page); confirm before stations.yaml.",
    ),
    ResortSpec(
        resort_id="castle-mountain",
        station_id="castle-mountain:CA:RESORT",
        country="CA",
        tz="America/Edmonton",
        url='https://www.skicastle.ca/snowreport/',
        parse=_cell('<td>Fresh Snow \\(24 hrs\\)</td>\\s*<td>([^<]{0,40})</td>'),
        report_time=_stamp('Last updated:\\s*(\\d{4}-\\d{2}-\\d{2} - \\d{1,2}:\\d{2})\\s*MT', '%Y-%m-%d - %H:%M', 'America/Edmonton'),
        unit="cm",
        report_hour_local=6,
        enabled=True,
        verified="2026-11-19: robots.txt (www.skicastle.ca) disallows only /wp-admin/ - /snowreport/ allowed for PowderBench; page and endpoint are the same host. No terms of use page (404); privacy policy has no scraping, automated-access or reproduction clause. Provenance: Castle's own server-rendered WordPress plugin (castle-mountain-live, built by Alpha V3) - a 'Mid Mountain - altitude (1915 m)' snow-fall table; the University of Lethbridge weather stations are linked for temperature/wind only, no aggregator credit. Wayback 2026-03-14 (id_, page as of 2026-03-13 evening): Fresh Snow (24 hrs) 6 (12 hrs 0, 48 hrs 19, 72 hrs 34, 7 days 46, season 622), 'Last updated: 2026-03-13 - 05:37 MT'. Live page fetched once: same row label, 24h 9, 'Last updated: 2026-11-18 - 09:34 MT' - parser and stamp both read it.",
        notes="Field is a fixed 'Fresh Snow (24 hrs)' row (12 h is a separate row). Winter posts ~05:30 MT; the 2026-03-14 capture taken at ~20:12 MT still showed the 05:37 report, so no midday re-post that day. report_hour_local=6 uses the 09:30 MT scrape; if late posts become common, 10 would move it to the 13:30 scrape.",
    ),
    ResortSpec(
        resort_id="silver-star",
        station_id="silver-star:CA:RESORT",
        country="CA",
        tz="America/Vancouver",
        url='https://api.skisilverstar.com/api/v1/dor/drupal/snow-reports?sort=date&direction=desc',
        parse=json_path(0, 'amount'),
        report_time=_stamp('"date":\\s*"(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2})"', '%Y-%m-%d %H:%M:%S', 'America/Vancouver'),
        unit="cm",
        report_hour_local=10,
        enabled=True,
        verified="2026-11-19: robots.txt: www.skisilverstar.com allows the snow-report page; api.skisilverstar.com/robots.txt redirects to the www homepage (HTML 200, which the production robots parser treats as allow-all) - so both page host and endpoint host allow PowderBench. No terms of use or trademark page (404s); privacy policy has no scraping, automated-access or reproduction clause. Owner Pacific Group Resorts (bought from POWDR 2025-11-25; sister resorts Jay Peak and Wintergreen are already enabled in the registry). Provenance: SilverStar's own Drupal 'DOR' API that its snow-report page's JS reads (staff-written report prose plus amount/overnight_amount/base_depth); no aggregator. Wayback 2025-12-11 (id_): first element 'Snow Report 2025-12-11 05:30:00', location Primary, amount 21, overnight_amount 20, computed 24_hour 21 / 48_hour 32 / 72_hour 34 / 7_day 52. Live API fetched once: latest 'Snow Report 2026-11-17 09:10:22', amount 0.",
        notes="Use json_path(0, 'amount') (list is sorted newest-first; computed.24_hour carries the same value and could be used instead). Report 'date' is local; posts seen at 05:30, 08:00-08:11 and (off-season) 09:10, so report_hour_local=10 sends every day to the afternoon scrape (12:30/13:30 local), after all observed posts. Feb 2026 had a reporting outage note; the freshness gate handles that. Note: this machine's tzdata gives America/Vancouver -07:00 for Nov 2026 (BC permanent daylight time), -08:00 for Dec 2025 - zoneinfo handles it.",
    ),
    ResortSpec(
        resort_id="mont-orford",
        station_id="mont-orford:CA:RESORT",
        country="CA",
        tz="America/Toronto",
        url='https://montorford.com/fr-ca/hiver/conditions-ski/',
        parse=_cell('data-key="today">\\s*<ul\\s+class="values list">\\s*<li\\s+class="entry"\\s+data-format="cm">\\s*<span class="value">([^<]{0,40})</span>'),
        report_time=_stamp('Modifié le (\\d{2} [A-Z][a-z]{2} \\d{4} à \\d{2}:\\d{2}:\\d{2})', '%d %b %Y à %H:%M:%S', 'America/Toronto'),
        unit="cm",
        report_hour_local=7,
        enabled=True,
        verified="2026-11-19: robots.txt (montorford.com) disallows only /admin - the conditions page is allowed for PowderBench; page and endpoint are the same host. No terms of use page found; privacy policy has no scraping, automated-access or reproduction clause; footer only says 'Copyright © 2018 La Corporation ski & golf Mont-Orford'. Provenance: Orford's own server-rendered conditions page (data-key 'today' / 'yesterday' blocks), says it is updated 'chaque matin avant 7h'; no maneige or other aggregator credit. Wayback 2026-02-13 (id_): today's snowfall 5 cm (48h 8, 7 days 30, season 404), 'Modifié le 13 Feb 2026 à 06:14:52'; 2025-12-08 capture read 4 (48h 4, 7d 41); other winter captures read 0. Live page fetched once: same markup, 0 cm, stamp 'Modifié le 18 Nov 2026 à 09:32:24' parses.",
        notes="Field is the 'today' (dernières 24 heures) accumulation block in cm. The stamp uses English month abbreviations ('Feb') even on the French page, so '%d %b %Y à %H:%M:%S' works in the C locale. The stamp is a page-modified time and can move later in the day for lift edits without changing the 24h value.",
    ),
)

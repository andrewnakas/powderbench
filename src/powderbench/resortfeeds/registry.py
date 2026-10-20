"""The resort registry: every scraped site, with its consent audit stamp.

Onboarding a resort (docs/RESORTS.md): recon the snow-report page, find the
site's OWN endpoint (never an aggregator), review robots.txt + ToS, write the
parser + a fixture test, record the outcome in `verified`, then set
`enabled=True` and add the station to data/stations.yaml. Entries that failed
review stay here disabled with the outcome in `notes` so nobody re-litigates
them.
"""

from __future__ import annotations

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
        notes="Mountain-wide 24h value ('0cm / 0in' format); page updates intraday ~14:00 CLT, so the 20:30 UTC scrape is the fresh capture.",
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
        notes="2026-10-20 held: first-party page, robots OK, site /policies has no clause, but the parent "
        "group's terms (foleyentertainmentgroup.com/terms-of-use) returned 403 — unverified, so disabled. "
        "Parser verified on Wayback (24h 10\" vs 7-day 25\").",
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
)

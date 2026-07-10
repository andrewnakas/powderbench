"""The resort registry: every scraped site, with its consent audit stamp.

Onboarding a resort (docs/RESORTS.md): recon the snow-report page, find the
site's OWN endpoint (never an aggregator), review robots.txt + ToS, write the
parser + a fixture test, record the outcome in `verified`, then set
`enabled=True` and add the station to data/stations.yaml. Entries that failed
review stay here disabled with the outcome in `notes` so nobody re-litigates
them.
"""

from __future__ import annotations

from .parse import json_path, json_row, regex_number
from .spec import ResortSpec

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
        url="https://ws.busplus.com.ar/centrosesqui/partediario/climas?Centro=CA",
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
        headers={"PUBLIC-KEY": "ij877HGyh74U&mmwsYH"},
        notes="nieveUltimas24 for Sector Superior (2000 m); Base/Intermedio sectors also available.",
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
        resort_id="whakapapa",
        station_id="whakapapa:NZ:RESORT",
        country="NZ",
        tz="Pacific/Auckland",
        url="https://www.whakapapa.com/snow-report",
        parse=_not_scraped,
        notes="2026-07-10 recon incomplete: report renders client-side, endpoint not identified in static HTML.",
    ),
    ResortSpec(
        resort_id="mt-buller",
        station_id="mt-buller:AU:RESORT",
        country="AU",
        tz="Australia/Melbourne",
        url="https://www.mtbuller.com.au/winter/snow-weather/snow-report",
        parse=_not_scraped,
        notes="2026-07-10 recon incomplete: robots/ToS fine (HubSpot CMS), but snow numbers render client-side; endpoint not identified.",
    ),
    ResortSpec(
        resort_id="portillo",
        station_id="portillo:CL:RESORT",
        country="CL",
        tz="America/Santiago",
        url="https://skiportillo.com/montana/clima-y-condiciones/",
        parse=_not_scraped,
        notes="2026-07-10 recon incomplete: robots allows all; wp-json mountain post exists but ACF fields are hidden; conditions render client-side.",
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
)

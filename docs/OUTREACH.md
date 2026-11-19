# Data-permission outreach

PowderBench only republishes data whose licence or owner allows it (see
[RESORTS.md](RESORTS.md) ground rules). Some of the best mountain snow data is
held back by terms of use rather than by availability, so we ask. Every grant
gets recorded here **and** in the source's `verified` stamp / `docs/DATA.md`
entry before anything is enabled.

## Tracker

| # | Who | What it unlocks | Contact | Status | Date | Notes |
|---|---|---|---|---|---|---|
| 1 | Synoptic Data (MesoWest) | Avalanche-centre & resort study-plot stations (NWAC, UAC, CAIC, resort plots) — hourly depth / interval snow | support@synopticdata.com | not sent | | Open Access tier is .edu-only and forbids redistribution; ask for a public-benchmark licence (we publish scores + daily truth values, never raw feeds) |
| 2 | Avalanche Canada | Weather-station API (`weather.prod.avalanche.ca`) — BC/AB HS, HN24, SWE | via avalanche.ca contact form / data-sharing policy | not sent | | Ask whether daily derived values may be republished with attribution |
| 3 | NWAC | ~50 Cascades/Olympics stations, 24h snow | nwac.us contact | not sent | | Their data portal has no posted licence |
| 4 | Utah Avalanche Center | Wasatch study plots | utahavalanchecenter.org contact | not sent | | Many plots feed Synoptic — #1 may cover it |
| 5 | Alta Ski Area | Collins study plot 24h snow (1980+), Guard House (1945+) history | snow safety / marketing | not sent | | The most famous snow record in the US |
| 6 | UC Berkeley CSSL | Real-time Donner Pass manual new snow | CSSL lead scientist | not sent | | History already open (Dryad, CC0) |
| 7 | Vail Resorts | US/CA resorts' own snow reports; also unblocks Perisher/Falls Creek/Hotham (AU) | corporate communications | not sent | | ToS currently prohibit automated access |
| 8 | Alterra Mountain Co. | Mammoth, Palisades, Steamboat, Winter Park, … snow reports | corporate communications | not sent | | |
| 9 | Mt Buller (AU) | `api.mtbuller.com.au` weather widget | resort marketing | not sent | | ToS: republication needs written approval |
| 10 | Jackson Hole, Grand Targhee, Whitefish | Resort snow reports (JH: clean first-party JSON of Bridger-Teton study plots) | snow safety / marketing | not sent | | JH + Targhee terms forbid redistribution without permission; Whitefish parent terms unreachable |
| 11 | Avalanche Canada (research) | Bulletin archive (~2011+), Mountain Information Network reports, weather stations, SNOWPACK model dashboard — for the snowpack training dataset | research@avalanche.ca | not sent | | Terms: no reproduction without written consent; Data Sharing Policy routes research/batch requests here (may need a signed agreement) |
| 12 | SnowPilot | 50k+ snow-pit profiles (CAAML), 2015– | SnowPilot team (snowpilot.org contact) | not sent | | No licence posted; "readily shared with researchers"; site 403s bots |
| 13 | SLF (data service) | Pérez-Guillén et al. 2022 danger-level ML dataset (EnviDat 330, "WSL data policy"), bulletin archive 1998– | data@slf.ch | not sent | | CC BY data already mirrored; this asks for the restricted sets |
| 15 | RCR (Fernie, Kicking Horse, Kimberley, Nakiska), Sun Peaks, Big White, Whitewater, Mt Bohemia, Beaver Mountain, Earl Holding (Sun Valley, Snowbasin), Aspen Snowmass | Resort snow reports (parsers ready for most) | marketing / snow safety | not sent | | Terms require written permission or are unpublished |
| 14 | National Avalanche Center / CAIC | avalanche.org forecast archive + observations API (Origin-allowlisted), CAIC field reports | NAC (avalanche.org) / CAIC | not sent | | CAIC ToS bans robots/data mining |

Status values: `not sent` → `sent` → `replied` → `granted` / `declined`.
On `declined`: record it, keep the source disabled, never re-litigate.

## Italian regional agencies (2026-11-19 recon)

The Alps league already uses Trentino (Meteotrentino), South Tyrol and Lombardy.
The other Italian Alpine regions need a message before anything is built:

| Agency | What it unlocks | Contact | Why a message is needed | Status |
|---|---|---|---|---|
| ARPA Piemonte | 79 automatic snow-depth gauges and 25 manual snow fields (Sestriere, Via Lattea, Bardonecchia, Limone, Macugnaga, Alagna), daily archive from 1987 | info.meteo@arpa.piemonte.it | Open data incl. APIs is CC BY 4.0 and documented for "machine to machine" use, but `utility.arpa.piemonte.it/robots.txt` is `Disallow: /`. Ask them to confirm a once-daily API read is welcome. Credit: "Fonte: Arpa Piemonte - www.arpa.piemonte.it" | not sent |
| ARPAV Veneto (Centro Valanghe di Arabba) | 42 snow-depth stations (Cortina, Arabba, Marmolada, Falcade, Asiago, Sappada) | via the ARPAV data "dispenser" form | CC BY 4.0, but the public API keeps ~2.7 days; history (hourly from 2010) only through the dispenser form, which asks for the requester's details. Request 2010–2025 HS for the nivo-meteo stations | not sent |
| Valle d'Aosta, Centro funzionale | 38 snow-depth gauges (Cervinia, Courmayeur, La Thuile, Pila, Champoluc, Gressoney) | u-idrografico@regione.vda.it, +39 0165 272749 | CC BY 4.0, but downloads go through a personal-details form and the JSON endpoints are internal. Ask for a sanctioned feed (or permission to poll) and a one-off historical export. The manual AINEVA fields (Ufficio neve e valanghe, u-valanghe@regione.vda.it) are not open | not sent |
| ARPA FVG – OSMER | 58 automatic gauges and 14 manual fields (Sella Nevea, Piancavallo, Tarvisio, Zoncolan) | meteo@arpa.fvg.it | The site says CC BY-SA 3.0 IT with a 24 h embargo on real-time data, the developer pages say CC BY 3.0 IT and the regional portal IODL 2.0; the snow endpoint returns 401. Ask for access and which licence applies | not sent |

## Template — data provider (Synoptic, avalanche centres, labs)

> **Subject:** Permission request: daily snowfall values for an open forecasting benchmark
>
> Hi <name>,
>
> I run PowderBench (powderbench.com, open source at
> github.com/andrewnakas/powderbench), a free, public benchmark where weather
> models, researchers and AI forecasters compete to predict mountain snowfall
> 24–72 hours ahead. Every forecast is scored against ground truth, and the
> results are public.
>
> Your <stations / network> would make the benchmark far more useful to the
> snow-safety and forecasting community: <one line on why — e.g. "they're the
> only measured new-snow plots in the Cascades">.
>
> What we'd like to do:
> - fetch <daily / hourly> <24h new snow / snow depth / SWE> for <N stations>,
>   politely (once or twice a day, identified user agent);
> - publish the **daily derived snowfall values** used for scoring, the scores,
>   and charts, with prominent attribution and a link to <you>;
> - never resell, rebrand, or republish raw feeds.
>
> If it's useful, we're happy to add a "data provided by" credit on every page
> that uses it, share our QC flags back with you, or stop at any time on request.
>
> Would that be OK, and are there terms you'd like us to follow?
>
> Thanks,
> Andrew Nakas — PowderBench

## Template — resort

> **Subject:** Featuring <Resort>'s snow report on PowderBench
>
> Hi <name>,
>
> PowderBench (powderbench.com) is a free public contest where forecasters and
> weather models try to predict how much snow will fall at ski areas — and
> <Resort>'s own morning snow report would be the official answer key for
> <Resort>.
>
> We'd read your public snow report once or twice a day (identified, polite,
> no scraping of anything else), store the reported 24h snowfall, and credit
> and link <Resort> wherever it appears. Forecasters chasing your storms means
> more people watching your snow — and we'd happily stop if you ever ask.
>
> Is that something you'd be comfortable with? If you have a preferred feed or
> endpoint, even better.
>
> Thanks,
> Andrew Nakas — PowderBench

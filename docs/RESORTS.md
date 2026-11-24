# Resort scraping: how it works, and how to add a resort

The resorts league scores against each resort's own published snow report.
This doc is the contributor guide for the scraper behind it
(`src/powderbench/resortfeeds/`); the design rationale and QC rules live in
[DATA.md](DATA.md#ground-truth-resort-snow-reports-resorts-league).

## Architecture in one paragraph

Every resort is one declarative `ResortSpec` in
`src/powderbench/resortfeeds/registry.py`: a URL, a parser (usually a one-line
combinator from `parse.py`), a timezone, and a consent audit stamp. A cron
(`scrape-resorts.yml`, 20:30 & 13:00 UTC) fetches every *enabled* spec and
appends one immutable snapshot CSV per run to `data/resortreports/raw/` —
failures included, so the archive is also the audit trail. Truth
(`truth_sources._resort_daily`) and the era5 reference feed
(`obsfeeds._resorts_fetch`) both read the archive through one function,
`archive.daily_from_archive`, which dedupes (earliest scrape per attributed
day wins) and turns cumulative season totals into daily deltas. The live site
is never fetched at resolve time.

## Ground rules (non-negotiable)

- **No aggregators.** OnTheSnow, Snow-Forecast, OpenSnow, Infonieve, … their
  ToS prohibit scraping, and a resort page whose numbers are *fed by* an
  aggregator is equally off-limits (that's why Cardrona/Treble Cone are
  disabled).
- **Individual resort sites only, with a recorded review.** A spec may only
  set `enabled=True` when its `verified` field records the date, the
  robots.txt outcome, the ToS outcome, and the endpoint's provenance. The
  test suite enforces `enabled ⇒ verified`.
- **Politeness is enforced in code**: descriptive User-Agent, runtime
  robots.txt re-check every scrape, ≥3 s per-host spacing, ≤2 fetches per
  site per day. Keep it that way.
- If a resort asks us to stop: set `enabled=False`, keep the history, note
  the request in `notes`. Its station-days void from then on.

## Adding a resort, step by step

1. **Recon the snow report page.** Find where the number actually comes from:
   the site's own widget JSON/XML endpoint (best — check the page source and
   its JS bundles for `fetch(`/API URLs), embedded JSON (`__NEXT_DATA__`,
   JSON-LD), or server-rendered HTML. If the number only exists client-side
   with no reachable endpoint, stop — note it in the registry as recon
   incomplete (candidates get revisited when someone wants to run headless
   onboarding).
2. **Check provenance.** If the endpoint or the displayed data belongs to an
   aggregator, the resort is unusable; record that in `notes` so nobody
   re-litigates it.
3. **Review robots.txt** for the page *and* the endpoint host, and the site's
   Terms of Use for scraping/automated-access clauses. Quote what matters in
   the `verified` stamp. When in doubt, stay disabled.
4. **Write the spec** in `registry.py`. Prefer a `parse.py` combinator
   (`json_path`, `json_row`, `regex_number`, `css_number`, `script_json`).
   Pick the field closest to "24h snowfall, upper mountain"; if the site only
   publishes a season total, set `cumulative=True`. Set `report_hour_local`
   to the hour the morning report refreshes (look for an "updated at"
   timestamp), and `tz` to the resort's IANA zone.
5. **Save a fixture** — the real response, trimmed — to
   `tests/fixtures/resorts/<resort_id>.{json,xml,html}`, and add the expected
   parsed value to `FIXTURE_VALUES` in `tests/test_resortfeeds.py`.
6. **Test locally**: `pytest tests/test_resortfeeds.py` and
   `powderbench scrape-resorts --dry-run --only <resort_id>`.
7. **Add the station** to `data/stations.yaml` (`league: resorts`, id
   `<resort_id>:<CC>:RESORT`, coordinates of the resort, elevation the report
   refers to), extend `data/climatology/resorts.csv` for the new station
   (ERA5-derived until report history exists), flip `enabled=True`, and open
   a PR.

## Scrape schedule

Each site is fetched at most twice a day: southern-hemisphere resorts at 13:00
and 20:30 UTC, North American resorts at 16:30 and 20:30 UTC (after their
05:00–09:00 local morning reports). `powderbench scrape-resorts --hemisphere N|S`.

## North America (2026-10-20 recon)

Enabled (resorts-na league, 19): Alta, Mt Baker, Timberline, Mt Hood Meadows,
Sugar Bowl, Loveland, Telluride, Lake Louise, Marmot Basin, Red Mountain,
Le Massif, Jay Peak, Bolton Valley, Wolf Creek, Brundage, Bogus Basin, Mission
Ridge, 49 Degrees North, Mt Spokane — every parser verified against winter
Wayback snapshots of the live page, cross-checked against the page's own
48h/7-day figures. Reading rules: "Trace" = 0; a range ("2-4") = its midpoint;
`4"+` = 4. A spec may declare `report_time`: a report more than 30 h old at
scrape time is archived as `stale` and never counted (Red Mountain).
Also held: Revelstoke (rolling 24h, no timestamp), Panorama and Monarch (need
a freshness gate), Big White (IP clause), Sun Peaks, RCR resorts (Fernie,
Kicking Horse, Kimberley, Nakiska), Whitewater, Mt Bohemia, Beaver Mountain,
Sun Valley/Snowbasin (permission), Smugglers' Notch (overnight, not 24h), Mad
River Glen (undefined window), Whiteface (Cloudflare challenge), Powderhorn,
Lookout Pass, Silver Mountain, Sunshine Village (re-check in season).
Held: Whitefish (parent-company terms unreachable), Jackson Hole and Grand
Targhee (terms forbid redistribution without permission — on
[OUTREACH.md](OUTREACH.md)), Mt Rose ("new snow" isn't a fixed 24h).
Rejected by terms of use: Alterra (Mammoth, Palisades, Solitude, Crystal,
Steamboat, Winter Park, Sugarbush, Tremblant, A-Basin), POWDR (Snowbird,
Copper, Eldora, Killington, Mt Bachelor), Boyne (Brighton, Big Sky, Sugarloaf,
Sunday River), Bridger Bowl; Vail resorts by policy; Sierra-at-Tahoe
(OpenSnow-fed), Taos (OpenSnow widget + bot ban), Aspen Snowmass
(personal-use terms), Brian Head (MCP bot ban), Schweitzer (Alterra), Mt
Washington BC (OpenSnow).

## North America (2026-11-19 recon)

Four agents re-screened 140 more areas (Rockies/Southwest 29, West 25, East
and Midwest 50, Canada 36) under the same rules: own data only, robots and terms read,
and a winter Wayback capture cross-checked against the page's own 48h/7-day
figures. Eleven were enabled, bringing resorts-na to 48:

| Resort | Report | Freshness gate |
|---|---|---|
| Monarch (CO) | own `accumulation__table` on /conditions/ | "Last Updated" (fixes the October stale-report hold) |
| Sundance (UT) | staff-entered `snow-weather` rows | none (page says "updated daily at 5 AM") |
| Lost Trail (MT) | own Elementor report | "Snow Report for <date time>" |
| Lookout Pass (ID) | own PowderWatch form, summit block | "Last updated …, PST" |
| Leavenworth (WA) | club's own Drupal view, 24 HRS field | "Reported at" |
| Peek'n Peak (NY) | own conditions block | none |
| Shanty Creek (MI) | own Snowfall block (Schuss Mountain) | none |
| Panorama (BC) | own report page | "Updated at"; report hour 10 so the afternoon scrape counts (fixes the October late-post hold) |
| Castle Mountain (AB) | own table, Mid Mountain 1,915 m | "Last updated … MT" |
| SilverStar (BC) | own Drupal API, newest report's `amount` | report `date` |
| Mont Orford (QC) | own conditions page, last 24 h | "Modifié le" |

Station positions were checked against OpenSkiData (`data/skiareas/`).
The same pass found that a decimal typed without its leading zero (".5″")
parsed as 5; both number readers now read it as 0.5.

**Valle Nevado switched off.** Mountain Capital Partners has owned it since
2023, and MCP's website terms cover "each of our MCP Resort websites",
including those in Chile, and ban robots. Its history is kept. The same terms
rule out Purgatory, Sipapu, Pajarito, Arizona Snowbowl and Nordic Valley
(and Willamette Pass and Brian Head, already rejected).

Held (parser or source not ready): Echo Mountain (no timestamp; robots
carries an `ai-train=no` content signal), Sunrise Park (sporadic updates),
Granby Ranch, Powderhorn and Silver Mountain (2026 redesigns, no winter
capture yet), Sunlight (source unclear), Snow King, Belleayre, Gore and Blue
Knob (Cloudflare challenge), Antelope Butte, Mt Shasta Ski Park and Whaleback
(Google Sheets, empty off-season), Tahoe Donner (re-saved at random hours),
Pomerelle, Donner Ski Ranch, Mt Hood Skibowl, Loup Loup, Kissing Bridge,
SnowRiver, Lutsen, Granite Peak, Perfect North, Mount Kato, Black Mountain
ME, Trollhaugen (inconsistent, stale or single captures), Hoodoo (prose
report), Tenney and Hatley Pointe (parsers ready, re-check in season),
Waterville Valley and Pats Peak (own feeds never archived), Gunstock and
Cannon (no fixed 24h), Mount Abram, Swain, Cataloochee, Appalachian Ski Mtn,
Snow Trails (undefined window), Giants Ridge and Butternut (48h/7-day only),
Bryce and Ski Sundown (terms render only with JavaScript), Big Powderhorn,
Ski Brule, Canaan Valley (redesigns), Brundage (disabled after HTTP errors
from Actions; answers normally from home, so re-test once from a runner).
Canada held: Revelstoke (rolling 24h, no timestamp), Sunshine Village (no
24h in the new site's HTML), Mt Seymour and Troll (stamps without a year),
Grouse (several rolling reports a day), Sasquatch (ambiguous terms), Apex
(403), Shames (TLS error), Mont Sutton, Les Sommets, Searchmont, Mont Édouard
(no winter capture), Ski Wentworth, Val-d'Irène (stale, two-language stamps),
Le Valinouët (no stamp, resets to 0 overnight), Mont Grand-Fonds (posted the
afternoon before), Marble Mountain (no window or unit), Mount St. Louis
Moonstone (dry days read "no"; needs its own parser).
Permission needed ([OUTREACH.md](OUTREACH.md)): Beaver Mountain, Eagle
Point, Anthony Lakes, Diamond Peak, Mt Bohemia.

Rejected: Angel Fire (OpenSnow), Snowy Range (OnTheSnow), White Pine and
Soldier Mountain (SnoCountry), Magic Mountain ID (OnTheSnow), Lost Valley
(SnoCountry); by terms: Powder Mountain, Red Lodge, Ski Apache, Bromley,
Bretton Woods, Titus, Wild Mountain, California Mountain Resort Co. (Dodge
Ridge, Bear Valley, Mountain High — also robots `Disallow: /`), and the
October terms rejections (Windham, Greek Peak, Plattekill, Massanutten,
Whitecap) reconfirmed; Pebble Creek (data endpoint disallowed in robots);
Soda Springs (POWDR), Snow Valley (Alterra), Pleasant Mountain (Boyne);
Canada: Mt Norquay, Manning Park, Owl's Head, Ski Garceau (terms), Blue
Mountain ON and Tremblant (Alterra); no first-party 24h at Pass Powderkeg,
Hudson Bay Mountain, Mount Cain, Mount Sima, Harper Mountain, Massif du Sud.
No usable report: Ski Cooper (image only), Howelsen Hill (phone line),
Cherry Peak, Hogadon, Turner, Teton Pass, Kelly Canyon, Spout Springs,
Porcupine Mountains, Camelback, Spirit Mountain, Bristol, Mohawk Mountain,
Whitetooth, Mont Comi.

## Current registry status

See the table in [DATA.md](DATA.md) and the authoritative
`registry.py` — every entry carries its own verdict and date (60 live as of
2026-11-19: 48 in North America).

## Historical data leads (for backfilling, not yet wired)

- **Victorian government daily snow-depth CSVs** — "Victorian Alpine Resorts
  Daily Snow Depth Records" on Data Vic (CC-BY 4.0): Mt Buller, Falls Creek,
  Mt Hotham, Mt Baw Baw, Lake Mountain, Mt Stirling, 1993–2023, staff-measured
  daily depths. The CSV host sits behind a Cloudflare challenge; the 1993–2017
  files are mirrored on the Wayback Machine. Wide spreadsheet format — needs
  bespoke wrangling, but the licence is clean.
- **Snowy Hydro snow-course archive** — Spencers Creek / Deep Creek / Three
  Mile Dam, ~weekly measurements back to 1954, behind
  `wp-content/themes/snowyhydro/inc/getData.php?yearA=&yearB=` (JSON). The
  endpoint tarpits plain HTTP clients; one headless-browser session could pull
  the full series.
- Thredbo and NZSki publish no history (checked 2026-07-11); their archives
  start with our scraper.

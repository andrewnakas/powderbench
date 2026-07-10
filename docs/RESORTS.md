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

## Current registry status

See the table in [DATA.md](DATA.md) and the authoritative
`registry.py` — every entry carries its own verdict and date. Unprobed
southern-hemisphere candidates worth recon: Valle Nevado, La Parva, El
Colorado (CL), Chapelco, Cerro Castor, Las Leñas, Caviahue (AR), Tūroa (NZ).

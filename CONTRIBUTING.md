# Contributing to PowderBench

Thanks for helping! There are three ways in.

## 1. Compete
Submit forecasts — see [docs/SUBMITTING.md](docs/SUBMITTING.md). One command:
`powderbench submit my.csv --team my-team --league stations`.

## 2. Add or fix data
- **A new truth network** (stations with an open licence): add an adapter in
  `src/powderbench/networks/`, a generator in `scripts/networks/`, register a
  `TruthSource`, add fixture tests, and run the hindcast gate
  (`powderbench hindcast … --featured-only`) — see
  [docs/METHODOLOGY.md](docs/METHODOLOGY.md) §7. Record the licence in
  `data/sources.yaml`.
- **A resort**: follow [docs/RESORTS.md](docs/RESORTS.md). First-party pages
  only, robots + terms reviewed and quoted in the `verified` stamp, parser
  verified on archived winter pages.
- **A data problem** (a bad reading, a misplaced station): open a
  "Data issue" — QC fixes are applied to everyone.

## 3. Improve the code
`pip install -e '.[dev,grib]'`, then `pytest -q`. Keep changes small and
tested; the scoring and QC code is the benchmark, so changes there need a test
that shows the before/after.

By contributing you agree your code is released under the MIT licence.
Be kind — see the code of conduct below.

## Code of conduct
Be respectful, assume good faith, no harassment. Maintainers may remove
content or ban participants who don't follow this. Report problems by opening
an issue or contacting the maintainer via GitHub.

# Changelog

## Unreleased

- The headline metric is now called **Powder Accuracy** (same formula: 100 × (1 − MAE / MAE_climatology); higher is better). The JSON field stays `powder_score`.
- Site: snow forecast map with per-station best forecasts; plain-language scoring explainer; hourly forecast refresh.

## 0.3.0 — October 2026: northern-hemisphere scale-up

**Leagues and truth**
- Stations league grows from 45 to ~1,000 stations: the full SNOTEL network,
  CDEC Sierra pillows (hourly midnight readings), SNOLITE and BC snow pillows.
- New **Observers** league: measured new snow from ~1,160 COOP, CoCoRaHS and
  Environment Canada observers at mountain and ski-area sites, with
  per-station observation-day attribution fitted against ERA5.
- New **Alps** league: measured 24 h new snow at 216 Swiss sites (SLF) and 22
  Austrian climate stations (GeoSphere Austria): St. Anton, Obergurgl, Saalbach…
- New **Resorts · North America** league: 37 resorts' own morning reports,
  with a 30 h freshness gate on reports that carry a date.
- Featured stations for the headline board; full-network and regional side
  tracks.
- Storm Mode: storm-total event contests.

**Accuracy**
- QC additions: SWE-neighbour support (SNOLITE), density, stuck-sensor and
  spike checks (CDEC), provider flags (GHCN, ECCC), stale resort reports.
- Duplicate-site rule (no station within 1.5 km of a SNOTEL site).
- Climatology built from each station's own record; skill is computed on the
  rows climatology covers.

**Competitors**
- Baselines: ECMWF IFS, ECMWF AIFS, ICON, GEM, UKMO, NBM, HRRR, AROME, NOAA
  WPC percentiles, NWS NDFD snow amount (the forecast offices' own forecast), and the
  model consensus — each run 3 h before the cutoff.
- `powderbench submit`, a starter ensemble, a daily auto-submit template, an
  agent API (`/api/v1/open-rounds.json`, `/llms.txt`) and a Colab notebook for
  training a post-processing model.

- House agents: `house-blend`, `house-analog` (k-nearest analogs) and `house-gbm`
  (LightGBM quantiles, retrained daily); `powderbench agent-backtest`.
- LLM agents: `powderbench brief` (the round briefing) and `powderbench mcp` (MCP
  tools: open_rounds, get_brief, validate_forecast, submit_forecast), plus
  `examples/llm-agent.yml` for running a subscription-authenticated Claude Code
  agent daily.

- Team pages (`team.html?t=<team>`) with head-to-head records against the
  baselines, and embeddable SVG badges.

**Open data**
- `nakas/powderbench-truth` (QC'd daily truth) and
  `nakas/powderbench-snowpack` (snowpack/avalanche training data) on Hugging
  Face; `docs/METHODOLOGY.md`, `docs/SOURCES.md`, `CITATION.cff`.

**Fixes**
- Baselines run when due (2.5–9 h before the cutoff) from a 2-hourly job,
  instead of at fixed slots that GitHub started up to 5 h late. The submission
  deadline now applies to baselines and house agents too. 33 earlier rounds had
  baselines committed after the cutoff (see METHODOLOGY).
- Site publish no longer fails on leagues without rounds yet; baselines are
  pinned to their scheduled slot; map basemap switched to a keyless provider.

## 0.2.0 — July 2026: southern hemisphere
- ERA5 and Resorts leagues across New Zealand, Australia, Chile and Argentina;
  13 resort scrapers with audit stamps; season explorer; southern snow-station
  archive back to 1954.

## 0.1.0 — July 2026: launch
- Stations league (45 SNOTEL sites), daily rounds, Powder Score against
  climatology, NWP baselines, pull-request submissions with auto-merge.

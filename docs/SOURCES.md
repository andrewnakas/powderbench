# Data sources and attribution

<!-- generated from data/sources.yaml: edit that, then run python scripts/build_sources.py -->

Every source below permits the use PowderBench makes of it. Please keep these attributions when you reuse PowderBench data.

## Ground truth

| Source | Provider | Licence | Attribution |
|---|---|---|---|
| [NRCS SNOTEL, SNOLITE & AWDB (incl. BC snow pillows)](https://wcc.sc.egov.usda.gov/) | USDA Natural Resources Conservation Service | US public domain | Snow telemetry: USDA NRCS National Water and Climate Center (AWDB). |
| [CDEC snow pillows](https://cdec.water.ca.gov/) | California Department of Water Resources | Public data of the State of California | California snow pillows: CA Department of Water Resources, CDEC. |
| [GHCN-Daily (NWS COOP + CoCoRaHS)](https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily) | NOAA National Centers for Environmental Information | US public domain; CoCoRaHS observations © CoCoRaHS, CC BY 3.0 | Measured new snow: NOAA NCEI GHCN-Daily, including NWS Cooperative Observers and CoCoRaHS volunteers (cocorahs.org). |
| [Climate daily (TOTAL_SNOW)](https://api.weather.gc.ca/) | Environment and Climate Change Canada | Open Government Licence – Canada | Contains information licensed under the Open Government Licence – Canada (ECCC climate data). |
| [Measured new snow (HN) — study plots and IMIS](https://www.slf.ch/) | WSL Institute for Snow and Avalanche Research SLF | CC BY 4.0 | Swiss snow data © WSL Institute for Snow and Avalanche Research SLF (www.slf.ch), CC BY 4.0. |
| [Measured new snow (hns000d0) — manual precipitation stations](https://opendata.swiss/en/organization/bundesamt-fur-meteorologie-und-klimatologie-meteoschweiz) | MeteoSwiss (Federal Office of Meteorology and Climatology) | CC BY 4.0 | Source: MeteoSwiss. |
| [Measured new snow (shneu_manu) — climate stations](https://data.hub.geosphere.at/) | GeoSphere Austria | CC BY 4.0 | Austrian snow data © GeoSphere Austria (data.hub.geosphere.at), CC BY 4.0. |
| [Measured new snow (NSH_TAG) — precipitation stations](https://opendata.dwd.de/climate_environment/CDC/) | Deutscher Wetterdienst | CC BY 4.0 | German snow data © Deutscher Wetterdienst (opendata.dwd.de), CC BY 4.0. |
| [Measured new snow (HN) — snow fields](https://dati.meteotrentino.it/) | Provincia Autonoma di Trento — Meteotrentino | CC BY 4.0 | Trentino snow data © Provincia Autonoma di Trento — Meteotrentino, CC BY 4.0. |
| [Snow depth — avalanche-warning and hydrographic stations](https://www.data.gv.at/katalog/datasets/bb43170b-30fb-48aa-893f-51c60d27056f) | Land Tirol (LWD Tirol, HD Tirol) | CC BY 4.0 | Tyrolean snow data © Land Tirol (data.tirol.gv.at), CC BY 4.0. |
| [Misure meteo e idrografiche](https://data.civis.bz.it/dataset/misure-meteo-e-idrografiche) | Provincia autonoma di Bolzano – Alto Adige | CC0 1.0 | South Tyrol snow data: Provincia autonoma di Bolzano – Alto Adige (no attribution required; credited anyway). |
| [Altezza neve](https://www.dati.lombardia.it/d/uqbu-tt6m) (and the 2011–2020, pre-2010 and recent sensor datasets) | ARPA Lombardia | CC0 1.0 | Lombard snow data: ARPA Lombardia (credited anyway). |
| [Daily snowfall (降雪の深さの合計) — AMeDAS](https://www.data.jma.go.jp/stats/etrn/) | Japan Meteorological Agency | JMA terms of use (compatible with CC BY 4.0) | 出典：気象庁ホームページ (Japan Meteorological Agency). |
| [Snow depth — weather stations](https://opendata-download-metobs.smhi.se/) | SMHI (Swedish Meteorological and Hydrological Institute) | CC BY 4.0 | Swedish snow data © SMHI, CC BY 4.0. |
| [Snow depth — weather stations](https://en.ilmatieteenlaitos.fi/open-data) | Finnish Meteorological Institute | CC BY 4.0 | Finnish snow data © Finnish Meteorological Institute, CC BY 4.0. |
| [Snow depth and precipitation — weather stations (Frost API)](https://frost.met.no/) | MET Norway (Norwegian Meteorological Institute) | CC BY 4.0 / NLOD 2.0 (each Frost response cites CC BY 3.0 NO); free client id | Norwegian snow data from MET Norway (frost.met.no). |
| [Resort snow reports](https://github.com/andrewnakas/powderbench/blob/main/src/powderbench/resortfeeds/registry.py) | Each resort (first-party pages; per-resort audit in the registry) | Factual figures read from public pages, per-site robots/terms review; resorts may opt out at any time | Snow figures as published by each resort; see the resort registry for sources. |
| [ERA5 reanalysis (via Open-Meteo)](https://open-meteo.com/) | ECMWF / Copernicus Climate Change Service; Open-Meteo | CC BY 4.0 | Contains modified Copernicus Climate Change Service information; data via Open-Meteo.com (CC BY 4.0). |

## Reference forecasts

| Source | Provider | Licence | Attribution |
|---|---|---|---|
| [Weather-model forecasts (GFS, ECMWF IFS & AIFS, ICON, GEM, UKMO, NBM, HRRR, AROME)](https://open-meteo.com/) | Open-Meteo (from NOAA, ECMWF, DWD, ECCC, Met Office, Météo-France) | CC BY 4.0 | Weather forecasts by Open-Meteo.com (CC BY 4.0), from the respective national weather services. |
| [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data) (IFS 0.25°, AIFS), read directly | ECMWF (via the AWS Open Data bucket `ecmwf-forecasts`) | CC BY 4.0 | Forecasts: ECMWF open data, CC BY 4.0; daily station totals derived by PowderBench. |
| [NOAA GFS 0.25°](https://registry.opendata.aws/noaa-gfs-bdp-pds/), read directly | NOAA NCEP (via NOAA Open Data Dissemination) | US public domain | Forecasts: NOAA/NCEP Global Forecast System. |
| [WPC Probabilistic Winter Precipitation Forecast](https://www.wpc.ncep.noaa.gov/pwpf/) | NOAA Weather Prediction Center | US public domain | Snowfall percentiles: NOAA/NWS Weather Prediction Center. |
| [National Digital Forecast Database (NDFD) snow amount](https://registry.opendata.aws/noaa-ndfd/) | NOAA National Weather Service (via NOAA Open Data Dissemination) | US public domain | Snow amount forecasts: NOAA/NWS National Digital Forecast Database. |

## Metadata

| Source | Provider | Licence | Attribution |
|---|---|---|---|
| [Ski areas](https://openskidata.org/) | OpenSkiData (OpenStreetMap contributors, Skimap.org) | ODbL 1.0 | Ski areas © OpenSkiData / OpenStreetMap contributors, ODbL. |

## Training & history

| Source | Provider | Licence | Attribution |
|---|---|---|---|
| [Southern-hemisphere snow archives](https://github.com/andrewnakas/powderbench/blob/main/data/snowhistory/catalog.yaml) | Snowy Hydro; Victorian Alpine Resorts (Data Vic); INA and SNIH (Argentina) | CC BY (Data Vic, INA, SNIH); Snowy Hydro published data, attributed, removed on request | Historical snow series: Snowy Hydro Ltd; State of Victoria (CC BY); INA and SNIH, Argentina (CC BY). |
| [Snowpack & avalanche training data (Hugging Face mirror)](https://huggingface.co/datasets/nakas/powderbench-snowpack) | Météo-France; NVE (Norway); SLF/EnviDat; Zenodo authors | Per source: Etalab 2.0, NLOD 2.0, CC BY 4.0, CC BY-SA 4.0, ODbL | See the dataset card for per-source attribution. |

## PowderBench's own open data

- **QC'd daily truth** for every station league (with QC flags and station metadata): [huggingface.co/datasets/nakas/powderbench-truth](https://huggingface.co/datasets/nakas/powderbench-truth)
- **Snowpack & avalanche training data** (Météo-France, NVE, SLF/EnviDat, Zenodo — per-source licences): [huggingface.co/datasets/nakas/powderbench-snowpack](https://huggingface.co/datasets/nakas/powderbench-snowpack)
- **Every round, submission, truth file and result**: the [GitHub repository](https://github.com/andrewnakas/powderbench) (`data/`).

Takedown or correction requests: open an issue on GitHub — we act on them promptly.

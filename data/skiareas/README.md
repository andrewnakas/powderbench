# Ski areas (North America and the Alps)

`ski_areas_na.csv`: operating downhill ski areas with at least one lift in the
US and Canada, extracted from **OpenSkiData** (openskidata.org, data from
OpenStreetMap contributors and Skimap.org), licensed under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
This extract is itself made available under the ODbL. Refresh:
`tiles.openskimap.org/csv/ski_areas.csv` → filter `status=operating`,
`has_downhill=yes`, `lift_count>=1`, countries US/CA.

`ski_areas_alps.csv`: the same filters for Switzerland, Austria, France,
Italy, Germany and Liechtenstein (same source, licence and terms). Alps
station labels use ski areas with at least 4 lifts.

Used to (a) admit observer stations near ski areas at any elevation and (b)
label network stations with their nearest ski area.

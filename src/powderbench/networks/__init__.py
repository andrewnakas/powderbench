"""Ground-truth network adapters beyond SNOTEL/ERA5/resorts. Each module
exposes a `daily(ids, begin, end)` returning the shared truth frame
(station_id, date, snow24, valid, qc_flag) and is registered as a
TruthSource in truth_sources.py."""

# Fire Archive: a wildfire–satellite database

A PostgreSQL/PostGIS database that links every US wildfire to its satellite record, so a spread model
(neural ODE, CNN/ConvLSTM, graph neural network) or a physics model benchmark (e.g. FIRETEC) can ask for
a fire and get everything it needs:

- the incident record (NIFC InFORM / IRWIN), with cause, complexity and fuel codes **with their meanings**
- every perimeter ever mapped (WFIGS daily and final perimeters), plus perimeters traced from VIIRS detections
- every thermal detection (VIIRS S-NPP / NOAA-20 / NOAA-21, MODIS Terra / Aqua; GOES and FireSat ready),
  **linked to its fire** with a method and a confidence score
- LiDAR collections (USGS 3DEP) covering the fire **before and after**, imagery scenes, burn severity,
  fuels/terrain rasters and weather, by reference

Guide and 3D app: <https://acamaro.com/projects/wildfire-data/>

## Layout

```
schema/      DDL in load order (00 extensions … 70 views/functions)
ingest/      loaders: reference.py, nifc.py, firms.py, lidar.py (common.py = helpers)
link/        run_link.sh: link detections to fires month by month (function link_hotspots in schema/60_link.sql)
queries/     proposal/: the 20 questions from the original proposal, one SQL file each
export/      web_bundle.py (case-fire bundles for the app), lidar_sample.py (EPT reader), site_data.py
serving/     push_to_supabase.py: slim read-only copy for the web app
worker/      Cloudflare Worker: FIRMS proxy (key + CORS), GOES latest, Supabase RPC cache
tests/       pytest acceptance tests (schema, linking, the 20 questions)
```

## Quick start

```sh
# PostgreSQL 16+ with PostGIS 3.4+ (Postgres.app on macOS, or docker run postgis/postgis)
createdb wildfire
for f in schema/*.sql; do psql -d wildfire -v ON_ERROR_STOP=1 -f "$f"; done

python -m venv .venv && .venv/bin/pip install "psycopg[binary]" pandas requests pyarrow shapely "laspy[lazrs]" rasterio pyproj scipy pystac-client pillow pytest
cd ingest
python reference.py                                   # states, counties (Census), NIFC code lists
python nifc.py incidents --csv inform_fire_occurrence_data.csv --since 2020
python nifc.py perimeters --since 2020                # WFIGS final + daily perimeters (with geometry)
python firms.py files fire_archive_*.csv fire_nrt_*.csv   # FIRMS archive downloads
python lidar.py index && python lidar.py link          # 3DEP collections and pre/post coverage
cd ..
link/run_link.sh 2020-01 2025-12                      # months are independent: run years in parallel
psql -d wildfire -c "REFRESH MATERIALIZED VIEW mv_hotspot_state"
WF_DSN="dbname=wildfire" .venv/bin/python -m pytest tests -q
```

Environment: `WF_DSN` (default `dbname=wildfire host=localhost port=5432`), `FIRMS_MAP_KEY` for the FIRMS API
([free key](https://firms.modaps.eosdis.nasa.gov/api/map_key/)).

## Design notes

- **Geometry** is WGS 84; areas and distances use `geography` (metres on the ellipsoid). **Time** is `timestamptz`, UTC.
- **Confidence is never forced onto one scale.** MODIS reports 0–100 %, VIIRS reports low/nominal/high.
  `confidence_raw` keeps the source value, `confidence_pct` is filled only for MODIS, `confidence_class` puts
  every sensor on the FIRMS three-class scale (MODIS: <30 low, 30–79 nominal, ≥80 high).
- **Linking** (`link_hotspots`): 1.0 inside a perimeter mapped within ±12 h; 0.8 inside the final perimeter;
  ≤0.6 within 2 km, decaying with distance; 0.4 DBSCAN cluster within 5 km of a reported origin. Only during
  the fire's window (discovery −1 day … end +3 days); volcano/static/offshore detections are excluded; one fire
  per detection. Perimeters are cut with `ST_Subdivide` (≤64 vertices) so the spatial index stays fast.
- **Training pairs** (`vw_training_pair`): every known state time (perimeter or overpass) paired with the next,
  keeping the true `dt_hours`, which irregular-time models (neural ODEs) need.
- **Data quality** handled at load: placeholder dates (1970) and end dates before discovery become NULL;
  fire windows are never inverted; LiDAR points flagged *withheld* and noise classes 7/18 are dropped;
  LiDAR epochs are thinned to equal density before canopy heights are compared.

## Status of satellite sources (Nov 2026)

- Suomi-NPP VIIRS ended 2 Nov 2026; live VIIRS = NOAA-20 + NOAA-21 (`VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`).
- FireSat (Earth Fire Alliance): 3 operational satellites launched 7 Jul 2026; data to early adopters,
  academic licences planned 2027. `sensor` has a `FIRESAT` row; add an adapter in `ingest/` when access opens.
- NASA FIRMS has no CORS and needs a key → use the Worker. NASA GIBS (VIIRS + GOES fire layers) works in the browser.

## Licences of the data

NIFC, NASA FIRMS/GIBS, USGS 3DEP and US Census data are US public domain. Copernicus Sentinel-2 data is free
and open (attribution). EOX Sentinel-2 cloudless 2020 is CC BY-NC-SA 4.0. Check each source before redistribution.

The code, schema and guide were written with Claude (AI-generated, November 2026) and checked by running
every query against the loaded database.

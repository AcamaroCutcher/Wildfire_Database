-- Every instrument that contributes thermal detections, perimeters or imagery.
-- One row per platform + instrument, so "which satellite saw this?" is a simple join.

CREATE TABLE sensor (
    sensor_id      smallint PRIMARY KEY,
    code           text UNIQUE NOT NULL,       -- stable key used by ingest scripts
    platform       text NOT NULL,              -- satellite or aircraft
    instrument     text NOT NULL,
    operator       text,
    kind           text NOT NULL CHECK (kind IN ('active_fire', 'optical', 'lidar', 'airborne_ir')),
    resolution_m   real,                       -- nominal pixel size at nadir
    revisit_note   text,
    first_data     date,                       -- start of the record used here (NULL = not fixed)
    last_data      date,                       -- end of the record (NULL = still producing)
    firms_source   text,                       -- FIRMS API source name, when it has one
    notes          text
);

INSERT INTO sensor (sensor_id, code, platform, instrument, operator, kind, resolution_m, revisit_note, first_data, last_data, firms_source, notes) VALUES
 (1,  'VIIRS_SNPP',   'Suomi NPP', 'VIIRS', 'NASA/NOAA', 'active_fire', 375, '~2 passes/day', '2012-01-20', '2026-11-02', 'VIIRS_SNPP_SP',
      'NOAA ended S-NPP products on 2026-11-02; history remains available.'),
 (2,  'VIIRS_NOAA20', 'NOAA-20 (JPSS-1)', 'VIIRS', 'NOAA/NASA', 'active_fire', 375, '~2 passes/day', NULL, NULL, 'VIIRS_NOAA20_NRT', NULL),
 (3,  'VIIRS_NOAA21', 'NOAA-21 (JPSS-2)', 'VIIRS', 'NOAA/NASA', 'active_fire', 375, '~2 passes/day', NULL, NULL, 'VIIRS_NOAA21_NRT', NULL),
 (4,  'MODIS_TERRA',  'Terra', 'MODIS', 'NASA', 'active_fire', 1000, 'morning and night pass', '2000-11-01', NULL, 'MODIS_NRT', NULL),
 (5,  'MODIS_AQUA',   'Aqua',  'MODIS', 'NASA', 'active_fire', 1000, 'afternoon and night pass', '2002-07-04', NULL, 'MODIS_NRT', NULL),
 (6,  'GOES18_ABI',   'GOES-18 (GOES-West)', 'ABI', 'NOAA', 'active_fire', 2000, 'every 5 min (CONUS)', NULL, NULL, NULL, 'Fire/Hot Spot Characterization (FDC) product'),
 (7,  'GOES19_ABI',   'GOES-19 (GOES-East)', 'ABI', 'NOAA', 'active_fire', 2000, 'every 5 min (CONUS)', '2025-04-07', NULL, NULL, 'GOES-East since 7 Apr 2025'),
 (8,  'LANDSAT_OLI',  'Landsat 8/9', 'OLI/TIRS', 'USGS/NASA', 'active_fire', 30, '8 days (two satellites)', NULL, NULL, 'LANDSAT_NRT', 'FIRMS Landsat fire detections, US and Canada'),
 (9,  'FIRESAT',      'FireSat constellation', 'FireSat 6-channel imager', 'Earth Fire Alliance', 'active_fire', 80, '>= 2/day (2026), hourly by 2029', NULL, NULL, NULL,
      'Licensed data (early adopters 2026, academic licences planned 2027). Adapter in ingest/firesat.py.'),
 (20, 'SENTINEL2',    'Sentinel-2A/B/C', 'MSI', 'ESA/Copernicus', 'optical', 10, '~5 days', NULL, NULL, NULL, 'Pre/post imagery, dNBR'),
 (21, 'LANDSAT_SR',   'Landsat 8/9', 'OLI', 'USGS/NASA', 'optical', 30, '8 days', NULL, NULL, NULL, 'Pre/post imagery, dNBR'),
 (30, 'USGS_3DEP',    'Aircraft', 'Airborne laser scanner', 'USGS 3DEP', 'lidar', 1, 'project-based', NULL, NULL, NULL, 'Point clouds (EPT/COPC)'),
 (31, 'GEDI',         'ISS', 'GEDI', 'NASA', 'lidar', 25, 'sampled footprints', '2019-04-18', NULL, NULL, 'Canopy height/cover footprints'),
 (32, 'ICESAT2',      'ICESat-2', 'ATLAS', 'NASA', 'lidar', 100, '91-day repeat', '2018-10-13', NULL, NULL, 'ATL08 canopy/terrain segments'),
 (40, 'AIRBORNE_IR',  'Aircraft', 'Infrared line scanner', 'NIFC/NIROPS, CAL FIRE/FIRIS', 'airborne_ir', NULL, 'nightly on large fires', NULL, NULL, NULL, 'IR perimeters')
ON CONFLICT DO NOTHING;

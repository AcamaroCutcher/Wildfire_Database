-- Imagery, LiDAR, rasters and weather attached to fire events.
-- Big files (COG, COPC/EPT, Zarr) stay in object storage; the database stores where they are,
-- when they were taken, and how they relate to the fire.

-- Optical acquisitions (Sentinel-2, Landsat, HLS, VIIRS reflectance) before, during and after a fire.
CREATE TABLE scene (
    scene_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id      bigint NOT NULL REFERENCES fire_event(event_id) ON DELETE CASCADE,
    sensor_id     smallint NOT NULL REFERENCES sensor(sensor_id),
    phase         text NOT NULL CHECK (phase IN ('pre', 'during', 'post')),
    acquired_at   timestamptz NOT NULL,
    cloud_pct     real,
    stac_item     text,                        -- STAC item URL
    assets        jsonb NOT NULL DEFAULT '{}', -- band name -> COG href
    footprint     geometry(MultiPolygon, 4326),
    UNIQUE (event_id, stac_item)
);
CREATE INDEX scene_event_ix ON scene (event_id, phase, acquired_at);

-- LiDAR acquisitions (one row per USGS 3DEP project / work unit).
CREATE TABLE lidar_collection (
    collection_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sensor_id       smallint NOT NULL REFERENCES sensor(sensor_id),
    name            text UNIQUE NOT NULL,      -- e.g. 'USGS_LPC_OR_...' work unit
    acquired_from   date,
    acquired_to     date,
    ept_url         text,                      -- Entwine Point Tile (s3://usgs-lidar-public/.../ept.json)
    copc_stac       text,                      -- Planetary Computer 3dep-lidar-copc collection/item
    points_per_m2   real,
    boundary        geometry(MultiPolygon, 4326)
);
CREATE INDEX lidar_collection_boundary_gix ON lidar_collection USING gist (boundary);

-- Which LiDAR covers which fire, and whether it is from before or after the burn.
CREATE TABLE event_lidar (
    event_id        bigint NOT NULL REFERENCES fire_event(event_id) ON DELETE CASCADE,
    collection_id   bigint NOT NULL REFERENCES lidar_collection(collection_id),
    phase           text NOT NULL CHECK (phase IN ('pre', 'post')),
    days_from_fire  integer,                   -- negative = before discovery, positive = after containment
    coverage_frac   real CHECK (coverage_frac BETWEEN 0 AND 1),   -- share of the final perimeter covered
    PRIMARY KEY (event_id, collection_id)
);

-- Any raster tied to a fire: burn severity, canopy height models, fuels, terrain, canopy maps.
CREATE TABLE raster_asset (
    asset_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id      bigint NOT NULL REFERENCES fire_event(event_id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN (
                    'dnbr', 'rdnbr', 'severity_class',                 -- burn severity
                    'chm_pre', 'chm_post', 'chm_diff', 'dtm', 'dsm',   -- from LiDAR
                    'fbfm40', 'fbfm13', 'canopy_cover', 'canopy_height', 'canopy_base_height', 'canopy_bulk_density',  -- LANDFIRE
                    'dem', 'slope', 'aspect', 'canopy_height_1m', 'landcover')),
    source        text NOT NULL,              -- 'MTBS', 'derived:sentinel2', 'LANDFIRE LF2024', 'USGS 3DEP', ...
    vintage       text,                       -- year or version of the source data
    href          text NOT NULL,              -- COG URL or relative path
    res_m         real,
    crs           text,
    valid_at      timestamptz,
    stats         jsonb NOT NULL DEFAULT '{}',
    UNIQUE (event_id, kind, source, vintage)
);

-- Sampled spaceborne LiDAR canopy metrics (GEDI footprints, ICESat-2 ATL08 segments).
CREATE TABLE canopy_sample (
    sample_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sensor_id      smallint NOT NULL REFERENCES sensor(sensor_id),
    acquired_at    timestamptz NOT NULL,
    geom           geometry(Point, 4326) NOT NULL,
    canopy_h_m     real,                      -- RH98 (GEDI) or h_canopy (ATL08)
    canopy_cover   real,
    agbd_mg_ha     real,                      -- above-ground biomass density (GEDI L4A)
    quality_ok     boolean,
    product        text
);
CREATE INDEX canopy_sample_geom_gix ON canopy_sample USING gist (geom);
CREATE INDEX canopy_sample_time_ix  ON canopy_sample (acquired_at);

-- Weather near the fire (HRRR/RTMA grid cells or stations), hourly.
CREATE TABLE weather_obs (
    event_id     bigint NOT NULL REFERENCES fire_event(event_id) ON DELETE CASCADE,
    valid_at     timestamptz NOT NULL,
    source       text NOT NULL,                -- 'HRRR', 'RTMA', 'ASOS:KPDX', ...
    geom         geometry(Point, 4326),
    wind_u_ms    real,
    wind_v_ms    real,
    gust_ms      real,
    temp_c       real,
    rh_pct       real,
    PRIMARY KEY (event_id, source, valid_at)
);

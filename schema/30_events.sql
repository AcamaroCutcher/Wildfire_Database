-- Fire events and their perimeters over time.

CREATE TABLE fire_event (
    event_id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unique_fire_identifier  text UNIQUE,               -- NIFC/IRWIN: '2021-ORWIF-210460'
    irwin_id                uuid UNIQUE,
    mtbs_id                 text UNIQUE,               -- MTBS Event_ID, when mapped
    name                    text NOT NULL,
    incident_type           text REFERENCES lk_incident_type(code),
    cause_code              text REFERENCES lk_cause(code),
    complexity_code         text REFERENCES lk_complexity(code),
    fuel_group_code         text REFERENCES lk_fuel_group(code),
    fuel_model              text,
    fire_behavior           text,
    state_code              text REFERENCES us_state(code),     -- point of origin (POO)
    county_geoid            char(5) REFERENCES us_county(geoid),
    county_name             text,                               -- as reported, kept even if no geoid match
    agency_code             text REFERENCES agency(code),
    poo                     geometry(Point, 4326),              -- reported point of origin
    ignition_est            geometry(Point, 4326),              -- estimated from the earliest linked detections
    ignition_est_time       timestamptz,
    discovered_at           timestamptz,
    contained_at            timestamptz,
    controlled_at           timestamptz,
    out_at                  timestamptz,
    reported_acres          numeric,                            -- final size reported by the incident
    estimated_cost_usd      numeric,
    final_perimeter         geometry(MultiPolygon, 4326),       -- best final perimeter (WFIGS final / MTBS)
    final_perimeter_source  text,
    final_acres             numeric GENERATED ALWAYS AS (ST_Area(final_perimeter::geography) / 4046.8564224) STORED,
    utm_srid                integer GENERATED ALWAYS AS (32600 + floor((ST_X(poo) + 180) / 6)::int + 1) STORED,
    source_updated_at       timestamptz,
    loaded_at               timestamptz NOT NULL DEFAULT now(),
    CHECK (contained_at IS NULL OR discovered_at IS NULL OR contained_at >= discovered_at - interval '1 day')
);
CREATE INDEX fire_event_poo_gix      ON fire_event USING gist (poo);
CREATE INDEX fire_event_perim_gix    ON fire_event USING gist (final_perimeter);
CREATE INDEX fire_event_discovered_ix ON fire_event (discovered_at);
CREATE INDEX fire_event_name_ix      ON fire_event (lower(name));
CREATE INDEX fire_event_state_ix     ON fire_event (state_code, discovered_at);

COMMENT ON TABLE fire_event IS 'One row per incident. Keys from NIFC/IRWIN and MTBS so every source can be joined.';
COMMENT ON COLUMN fire_event.poo IS 'Point of origin as reported by the incident (InFORM/WFIGS).';
COMMENT ON COLUMN fire_event.ignition_est IS 'Ignition estimated from the earliest high-confidence linked hotspots (link/ignition.sql).';

-- Every perimeter ever mapped for a fire, from any source. Ordered by observed_at it is the fire's progression.
CREATE TABLE perimeter_obs (
    perimeter_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id        bigint NOT NULL REFERENCES fire_event(event_id) ON DELETE CASCADE,
    observed_at     timestamptz NOT NULL,
    source          text NOT NULL CHECK (source IN ('WFIGS', 'MTBS', 'FIRIS', 'NIROPS', 'FIRESAT', 'VIIRS_DERIVED', 'GOES_DERIVED', 'OTHER')),
    source_id       text,                      -- e.g. WFIGS GlobalID
    map_method      text,                      -- 'Infrared Image', 'GPS-Walked', 'Mixed Methods', 'concave hull k=0.3', ...
    is_final        boolean NOT NULL DEFAULT false,
    reported_acres  numeric,                   -- GISAcres as delivered
    geom            geometry(MultiPolygon, 4326) NOT NULL,
    acres           numeric GENERATED ALWAYS AS (ST_Area(geom::geography) / 4046.8564224) STORED,
    UNIQUE (source, source_id),
    CHECK (ST_IsValid(geom))
);
CREATE INDEX perimeter_obs_event_time_ix ON perimeter_obs (event_id, observed_at);
CREATE INDEX perimeter_obs_geom_gix      ON perimeter_obs USING gist (geom);

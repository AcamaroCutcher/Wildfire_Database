-- Reference ("lookup") tables.
-- Codes are loaded together with their descriptions from the source's own coded-value domains
-- (NIFC ArcGIS layer metadata), so a code never appears without its meaning.

CREATE TABLE lk_incident_type (
    code        text PRIMARY KEY,              -- e.g. WF, RX, CX
    description text NOT NULL                  -- e.g. Wildfire, Prescribed Fire, Complex
);

CREATE TABLE lk_cause (
    code        text PRIMARY KEY,              -- source code, kept as text (NIFC uses both numbers and words)
    description text NOT NULL,
    cause_class text                           -- Human / Natural / Undetermined, when the source gives it
);

CREATE TABLE lk_complexity (
    code        text PRIMARY KEY,              -- e.g. 'Type 1 Incident'
    level       smallint CHECK (level BETWEEN 1 AND 5),   -- 1 = most complex
    description text
);

CREATE TABLE lk_fuel_group (
    code        text PRIMARY KEY,              -- e.g. 'Timber', 'Grass-Shrub'
    description text
);

CREATE TABLE us_state (
    code  text PRIMARY KEY,                    -- ISO 3166-2, as NIFC writes it: 'US-OR'
    usps  char(2) UNIQUE NOT NULL,             -- 'OR'
    name  text NOT NULL,
    geom  geometry(MultiPolygon, 4326)
);

CREATE TABLE us_county (
    geoid      char(5) PRIMARY KEY,            -- state FIPS + county FIPS
    state_code text NOT NULL REFERENCES us_state(code),
    name       text NOT NULL,
    geom       geometry(MultiPolygon, 4326)
);
CREATE INDEX us_county_geom_gix ON us_county USING gist (geom);
CREATE INDEX us_county_name_ix  ON us_county (state_code, lower(name));

CREATE TABLE agency (
    code text PRIMARY KEY,                     -- e.g. USFS, BLM, ODF
    name text
);

-- Seed values that are stable and documented by NWCG/NIFC. Everything else is loaded by ingest/nifc.py.
INSERT INTO lk_incident_type (code, description) VALUES
    ('WF', 'Wildfire'),
    ('RX', 'Prescribed Fire'),
    ('CX', 'Complex')
ON CONFLICT DO NOTHING;

INSERT INTO lk_complexity (code, level, description) VALUES
    ('Type 1 Incident', 1, 'Most complex: national-level team'),
    ('Type 2 Incident', 2, 'Regional or national team'),
    ('Type 3 Incident', 3, 'Extended attack, local team'),
    ('Type 4 Incident', 4, 'Initial attack, several resources'),
    ('Type 5 Incident', 5, 'Initial attack, one or two resources')
ON CONFLICT DO NOTHING;

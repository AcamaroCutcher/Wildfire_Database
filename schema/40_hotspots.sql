-- Thermal detections from every satellite, partitioned by month.
--
-- Confidence is NOT forced onto one scale, because the sensors don't share one:
--   MODIS gives a percentage 0-100; VIIRS gives a class: l(ow), n(ominal), h(igh); GOES gives a mask category.
-- confidence_raw keeps exactly what the source said, confidence_pct is filled only when the source
-- really is a percentage, and confidence_class puts everyone on the FIRMS three-class scale
-- (MODIS: 0-29 low, 30-79 nominal, 80-100 high).

CREATE TABLE hotspot (
    hotspot_id        bigint GENERATED ALWAYS AS IDENTITY,
    sensor_id         smallint NOT NULL REFERENCES sensor(sensor_id),
    acq_time          timestamptz NOT NULL,       -- acquisition date + time (UTC), not text
    latitude          double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude         double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    geom              geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,
    scan_km           real,                       -- pixel size across track
    track_km          real,                       -- pixel size along track
    brightness_k      real,                       -- MODIS band 21/22, VIIRS I4 brightness temperature
    brightness2_k     real,                       -- MODIS band 31, VIIRS I5
    frp_mw            real,                       -- fire radiative power
    confidence_raw    text,
    confidence_pct    smallint CHECK (confidence_pct BETWEEN 0 AND 100),
    confidence_class  text NOT NULL CHECK (confidence_class IN ('low', 'nominal', 'high')),
    daynight          char(1) CHECK (daynight IN ('D', 'N')),
    fire_type         smallint,                   -- FIRMS archive "type": 0 vegetation fire, 1 volcano, 2 other static source, 3 offshore
    data_version      text NOT NULL,              -- 'SP' (standard), 'NRT', 'RT', 'URT'
    product           text,                       -- e.g. 'VNP14IMG C2', 'MCD14ML 6.1'
    PRIMARY KEY (hotspot_id, acq_time),
    UNIQUE (sensor_id, acq_time, latitude, longitude)
) PARTITION BY RANGE (acq_time);

CREATE INDEX hotspot_geom_gix   ON hotspot USING gist (geom);
CREATE INDEX hotspot_time_brin  ON hotspot USING brin (acq_time);
CREATE INDEX hotspot_sensor_ix  ON hotspot (sensor_id, acq_time);

-- Partitions are monthly. Call ensure_hotspot_partitions() before loading a new period.
CREATE OR REPLACE FUNCTION ensure_hotspot_partitions(p_from date, p_to date)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    m date := date_trunc('month', p_from)::date;
    n integer := 0;
    tbl text;
BEGIN
    WHILE m <= p_to LOOP
        tbl := format('hotspot_%s', to_char(m, 'YYYY_MM'));
        IF to_regclass(tbl) IS NULL THEN
            EXECUTE format('CREATE TABLE %I PARTITION OF hotspot FOR VALUES FROM (%L) TO (%L)',
                           tbl, m::timestamptz, (m + interval '1 month')::timestamptz);
            n := n + 1;
        END IF;
        m := (m + interval '1 month')::date;
    END LOOP;
    RETURN n;
END $$;

CREATE TABLE hotspot_default PARTITION OF hotspot DEFAULT;

-- Helper: the FIRMS three-class scale for any sensor.
CREATE OR REPLACE FUNCTION confidence_class_of(p_raw text, p_pct smallint)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_pct IS NOT NULL THEN CASE WHEN p_pct >= 80 THEN 'high' WHEN p_pct >= 30 THEN 'nominal' ELSE 'low' END
        WHEN lower(p_raw) IN ('h', 'high')    THEN 'high'
        WHEN lower(p_raw) IN ('n', 'nominal') THEN 'nominal'
        ELSE 'low'
    END
$$;

-- Which fire a hotspot belongs to, and how we know. The old design had no such link at all.
CREATE TABLE hotspot_event_link (
    hotspot_id   bigint NOT NULL,
    acq_time     timestamptz NOT NULL,
    event_id     bigint NOT NULL REFERENCES fire_event(event_id) ON DELETE CASCADE,
    method       text NOT NULL CHECK (method IN ('inside_perimeter_same_time', 'inside_final_perimeter', 'buffer', 'cluster')),
    distance_m   real NOT NULL DEFAULT 0,      -- 0 when inside the perimeter
    dt_hours     real,                         -- hotspot time minus the matched perimeter time (or discovery)
    score        real NOT NULL CHECK (score BETWEEN 0 AND 1),
    linked_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hotspot_id, acq_time),
    FOREIGN KEY (hotspot_id, acq_time) REFERENCES hotspot (hotspot_id, acq_time) ON DELETE CASCADE
);
CREATE INDEX hotspot_event_link_event_ix ON hotspot_event_link (event_id, acq_time);

-- Views and functions used by the queries, the ML exports and the web app.

-- One readable row per incident: what the proposal calls the historical record.
CREATE OR REPLACE VIEW vw_wildfire_historical_record AS
SELECT e.event_id,
       e.unique_fire_identifier,
       e.name                                   AS incident_name,
       e.incident_type,
       t.description                            AS fire_type,
       s.usps                                   AS state,
       s.name                                   AS state_name,
       coalesce(c.name, e.county_name)          AS county,
       a.code                                   AS agency,
       e.discovered_at,
       e.contained_at,
       coalesce(e.reported_acres, e.final_acres) AS acres_burned,
       e.complexity_code,
       x.level                                  AS complexity_level,
       e.cause_code,
       cs.description                           AS cause
FROM fire_event e
LEFT JOIN lk_incident_type t ON t.code = e.incident_type
LEFT JOIN us_state s        ON s.code = e.state_code
LEFT JOIN us_county c       ON c.geoid = e.county_geoid
LEFT JOIN agency a          ON a.code = e.agency_code
LEFT JOIN lk_complexity x   ON x.code = e.complexity_code
LEFT JOIN lk_cause cs       ON cs.code = e.cause_code;

-- Everything we hold about each fire, counted: the "can I train on this fire?" view.
CREATE OR REPLACE VIEW vw_event_summary AS
SELECT e.event_id, e.name, e.discovered_at, coalesce(e.reported_acres, e.final_acres) AS acres,
       (SELECT count(*) FROM perimeter_obs p WHERE p.event_id = e.event_id)                         AS perimeters,
       (SELECT count(DISTINCT date_trunc('day', p.observed_at)) FROM perimeter_obs p WHERE p.event_id = e.event_id) AS perimeter_days,
       (SELECT count(*) FROM hotspot_event_link l WHERE l.event_id = e.event_id)                    AS hotspots,
       (SELECT count(*) FROM vw_overpass o WHERE o.event_id = e.event_id)                           AS overpasses,
       (SELECT count(*) FROM scene s WHERE s.event_id = e.event_id AND s.phase = 'pre')             AS pre_scenes,
       (SELECT count(*) FROM scene s WHERE s.event_id = e.event_id AND s.phase = 'post')            AS post_scenes,
       EXISTS (SELECT 1 FROM event_lidar x WHERE x.event_id = e.event_id AND x.phase = 'pre')       AS lidar_pre,
       EXISTS (SELECT 1 FROM event_lidar x WHERE x.event_id = e.event_id AND x.phase = 'post')      AS lidar_post,
       EXISTS (SELECT 1 FROM raster_asset r WHERE r.event_id = e.event_id AND r.kind IN ('dnbr', 'rdnbr')) AS has_severity,
       EXISTS (SELECT 1 FROM raster_asset r WHERE r.event_id = e.event_id AND r.kind = 'fbfm40')    AS has_fuels,
       (SELECT count(*) FROM weather_obs w WHERE w.event_id = e.event_id)                           AS weather_hours
FROM fire_event e;

-- Training pairs: every time we know the fire's state (a mapped perimeter or a satellite overpass),
-- paired with the next such time. Inputs are the state at t, the label is the state at t_next.
-- dt varies, which is exactly what a neural ODE wants; fixed-step models can resample.
CREATE OR REPLACE VIEW vw_training_pair AS
WITH times AS (
    SELECT event_id, observed_at AS t, 'perimeter:' || source AS kind FROM perimeter_obs
    UNION
    SELECT event_id, pass_end, 'overpass:' || sensor_id FROM vw_overpass
), ordered AS (
    SELECT event_id, t, kind,
           lead(t)    OVER (PARTITION BY event_id ORDER BY t) AS t_next,
           lead(kind) OVER (PARTITION BY event_id ORDER BY t) AS kind_next,
           lag(t)     OVER (PARTITION BY event_id ORDER BY t) AS t_prev
    FROM times
)
SELECT o.event_id, o.t, o.t_next,
       extract(epoch FROM o.t_next - o.t) / 3600.0                                   AS dt_hours,
       o.kind, o.kind_next,
       (SELECT p.perimeter_id FROM perimeter_obs p WHERE p.event_id = o.event_id AND p.observed_at <= o.t
         ORDER BY p.observed_at DESC LIMIT 1)                                        AS perimeter_at_t,
       (SELECT p.perimeter_id FROM perimeter_obs p WHERE p.event_id = o.event_id AND p.observed_at <= o.t_next
         ORDER BY p.observed_at DESC LIMIT 1)                                        AS perimeter_at_t_next,
       (SELECT count(*) FROM hotspot_event_link l WHERE l.event_id = o.event_id
          AND l.acq_time > coalesce(o.t_prev, '-infinity') AND l.acq_time <= o.t)    AS hotspots_since_prev,
       (SELECT count(*) FROM hotspot_event_link l WHERE l.event_id = o.event_id
          AND l.acq_time > o.t AND l.acq_time <= o.t_next)                           AS hotspots_to_next,
       (SELECT avg(sqrt(w.wind_u_ms ^ 2 + w.wind_v_ms ^ 2)) FROM weather_obs w WHERE w.event_id = o.event_id
          AND w.valid_at BETWEEN o.t AND o.t_next)                                   AS mean_wind_ms
FROM ordered o
WHERE o.t_next IS NOT NULL AND o.t_next > o.t;

-- JSON bundle for one fire, used by the web app (and handy for quick looks).
CREATE OR REPLACE FUNCTION event_bundle(p_event bigint, p_max_hotspots integer DEFAULT 20000)
RETURNS jsonb LANGUAGE sql STABLE AS $$
SELECT jsonb_build_object(
  'event', (SELECT to_jsonb(r) - 'event_id' FROM vw_wildfire_historical_record r WHERE r.event_id = p_event)
           || jsonb_build_object(
                'event_id', p_event,
                'poo', (SELECT ST_AsGeoJSON(poo, 6)::jsonb FROM fire_event WHERE event_id = p_event),
                'ignition_est', (SELECT ST_AsGeoJSON(ignition_est, 6)::jsonb FROM fire_event WHERE event_id = p_event),
                'ignition_est_time', (SELECT ignition_est_time FROM fire_event WHERE event_id = p_event),
                'bbox', (SELECT ST_AsGeoJSON(ST_Envelope(coalesce(final_perimeter, ST_Buffer(poo, 0.05))), 5)::jsonb FROM fire_event WHERE event_id = p_event)),
  'perimeters', coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'type', 'Feature', 'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.0002), 5)::jsonb,
                    'properties', jsonb_build_object('t', p.observed_at, 'source', p.source, 'method', p.map_method, 'acres', round(p.acres)))
                  ORDER BY p.observed_at) FROM perimeter_obs p WHERE p.event_id = p_event), '[]'),
  'hotspots', coalesce((SELECT jsonb_agg(jsonb_build_array(s.code, h.acq_time, round(h.longitude::numeric, 5), round(h.latitude::numeric, 5),
                                                          h.frp_mw, h.confidence_class, round(l.score::numeric, 2)) ORDER BY h.acq_time)
                FROM (SELECT * FROM hotspot_event_link WHERE event_id = p_event ORDER BY acq_time LIMIT p_max_hotspots) l
                JOIN hotspot h USING (hotspot_id, acq_time) JOIN sensor s ON s.sensor_id = h.sensor_id), '[]'),
  'hotspot_columns', jsonb_build_array('sensor', 'time', 'lon', 'lat', 'frp_mw', 'confidence', 'link_score'),
  'scenes', coalesce((SELECT jsonb_agg(jsonb_build_object('phase', phase, 'time', acquired_at, 'sensor', (SELECT code FROM sensor WHERE sensor_id = sc.sensor_id),
                    'cloud_pct', cloud_pct, 'stac_item', stac_item, 'assets', assets) ORDER BY acquired_at) FROM scene sc WHERE sc.event_id = p_event), '[]'),
  'lidar', coalesce((SELECT jsonb_agg(jsonb_build_object('phase', x.phase, 'name', c.name, 'from', c.acquired_from, 'to', c.acquired_to,
                    'ept', c.ept_url, 'copc', c.copc_stac, 'days_from_fire', x.days_from_fire, 'coverage', x.coverage_frac))
                FROM event_lidar x JOIN lidar_collection c USING (collection_id) WHERE x.event_id = p_event), '[]'),
  'rasters', coalesce((SELECT jsonb_agg(jsonb_build_object('kind', kind, 'source', source, 'vintage', vintage, 'href', href, 'res_m', res_m))
                FROM raster_asset WHERE event_id = p_event), '[]')
)
$$;

-- Fires whose extent touches a box and whose window overlaps a time range (for the app's fire list).
CREATE OR REPLACE FUNCTION fires_near(p_west float8, p_south float8, p_east float8, p_north float8,
                                      p_from timestamptz DEFAULT '-infinity', p_to timestamptz DEFAULT 'infinity',
                                      p_min_acres numeric DEFAULT 1000)
RETURNS TABLE (event_id bigint, name text, discovered_at timestamptz, acres numeric, lon float8, lat float8,
               perimeters bigint, hotspots bigint)
LANGUAGE sql STABLE AS $$
    SELECT e.event_id, e.name, e.discovered_at, round(coalesce(e.reported_acres, e.final_acres)),
           ST_X(coalesce(e.poo, ST_PointOnSurface(e.final_perimeter))), ST_Y(coalesce(e.poo, ST_PointOnSurface(e.final_perimeter))),
           (SELECT count(*) FROM perimeter_obs p WHERE p.event_id = e.event_id),
           (SELECT count(*) FROM hotspot_event_link l WHERE l.event_id = e.event_id)
    FROM fire_event e
    WHERE coalesce(e.final_perimeter, e.poo) && ST_MakeEnvelope(p_west, p_south, p_east, p_north, 4326)
      AND coalesce(e.reported_acres, e.final_acres, 0) >= p_min_acres
      AND fire_window(e) && tstzrange(p_from, p_to)
    ORDER BY e.discovered_at DESC
$$;

-- Which state each detection fell in (point-in-polygon on subdivided state outlines, so it stays fast
-- for millions of points). Refresh after loading hotspots: REFRESH MATERIALIZED VIEW mv_hotspot_state;
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_hotspot_state AS
WITH parts AS (SELECT code, ST_Subdivide(geom, 128) AS g FROM us_state)
SELECT DISTINCT ON (h.hotspot_id, h.acq_time) h.hotspot_id, h.acq_time, h.sensor_id, p.code AS state_code
FROM hotspot h
JOIN parts p ON ST_Intersects(p.g, h.geom)        -- a point on a shared edge matches twice; keep one
ORDER BY h.hotspot_id, h.acq_time, p.code
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS mv_hotspot_state_pk ON mv_hotspot_state (hotspot_id, acq_time);
CREATE INDEX IF NOT EXISTS mv_hotspot_state_state_ix ON mv_hotspot_state (state_code, acq_time);

-- Linking satellite detections to fire events.
--
-- link_hotspots(from, to, buffer_m) decides, for every hotspot acquired in [from, to), which fire it
-- belongs to and records how confident that link is:
--
--   score 1.0  inside_perimeter_same_time   inside a perimeter of the fire mapped within ±12 h
--   score 0.8  inside_final_perimeter       inside the fire's final perimeter, during the fire
--   score ≤0.6 buffer                       within buffer_m of the final perimeter (or the point of
--                                           origin when no perimeter exists), decaying with distance
--   score 0.4  cluster                      leftover detections that form a DBSCAN cluster whose hull
--                                           lies within 5 km of a fire's point of origin
--
-- "During the fire" means acq_time in [discovered - 1 day, end + 3 days], where end is the first known
-- of out / controlled / contained / last mapped perimeter, else discovered + 60 days.
-- Detections flagged by FIRMS as volcanoes, static industrial sources or offshore (type 1-3) are never linked.
-- Each hotspot gets at most one link: the highest score, then the nearest, then the closest in time.
-- The function is idempotent: it deletes the links in the window and rebuilds them.

CREATE OR REPLACE FUNCTION fire_window(e fire_event)
RETURNS tstzrange LANGUAGE sql STABLE AS $$
    -- start: discovery (or first mapped perimeter) - 1 day; end: first known end + 3 days.
    -- End times entered before the start (data errors) are ignored, so the window is never inverted.
    WITH b AS (
        SELECT coalesce(e.discovered_at, (SELECT min(observed_at) FROM perimeter_obs p WHERE p.event_id = e.event_id)) AS t0
    ), en AS (
        SELECT b.t0, coalesce(
                 CASE WHEN e.out_at        >= b.t0 THEN e.out_at END,
                 CASE WHEN e.controlled_at >= b.t0 THEN e.controlled_at END,
                 CASE WHEN e.contained_at  >= b.t0 THEN e.contained_at END,
                 (SELECT max(observed_at) FROM perimeter_obs p WHERE p.event_id = e.event_id AND p.observed_at >= b.t0),
                 b.t0 + interval '60 days') AS t1
        FROM b
    )
    SELECT tstzrange(t0 - interval '1 day', greatest(t1, t0) + interval '3 days', '[)') FROM en
$$;

CREATE OR REPLACE FUNCTION link_hotspots(p_from timestamptz, p_to timestamptz, p_buffer_m real DEFAULT 2000)
RETURNS TABLE (method text, links bigint) LANGUAGE plpgsql AS $$
-- Perimeters can have >100,000 vertices, so they are cut into small pieces (ST_Subdivide, <= 64 vertices)
-- once per run; hotspots are then compared only with nearby pieces through the spatial index.
BEGIN
    DELETE FROM hotspot_event_link l WHERE l.acq_time >= p_from AND l.acq_time < p_to;

    CREATE TEMP TABLE _ev ON COMMIT DROP AS
    SELECT e.event_id, fire_window(e) AS win, e.final_perimeter IS NOT NULL AS has_perimeter, e.poo, e.discovered_at
    FROM fire_event e
    WHERE (e.final_perimeter IS NOT NULL OR e.poo IS NOT NULL)
      AND fire_window(e) && tstzrange(p_from, p_to, '[)');
    CREATE UNIQUE INDEX ON _ev (event_id);

    -- final perimeters (or the point of origin when there is none), in pieces
    CREATE TEMP TABLE _piece ON COMMIT DROP AS
    SELECT ev.event_id, ST_Subdivide(e.final_perimeter, 64) AS g FROM _ev ev JOIN fire_event e USING (event_id) WHERE ev.has_perimeter
    UNION ALL
    SELECT event_id, poo FROM _ev WHERE NOT has_perimeter;
    CREATE INDEX ON _piece USING gist (g);

    -- dated perimeters near the window, in pieces
    CREATE TEMP TABLE _pobs ON COMMIT DROP AS
    SELECT p.event_id, p.observed_at, ST_Subdivide(p.geom, 64) AS g
    FROM perimeter_obs p JOIN _ev USING (event_id)
    WHERE p.observed_at >= p_from - interval '12 hours' AND p.observed_at < p_to + interval '12 hours';
    CREATE INDEX ON _pobs USING gist (g);

    CREATE TEMP TABLE _h ON COMMIT DROP AS
    SELECT h.hotspot_id, h.acq_time, h.geom, h.latitude
    FROM hotspot h
    WHERE h.acq_time >= p_from AND h.acq_time < p_to AND coalesce(h.fire_type, 0) = 0;
    CREATE INDEX ON _h USING gist (geom);
    ANALYZE _ev; ANALYZE _piece; ANALYZE _pobs; ANALYZE _h;

    INSERT INTO hotspot_event_link (hotspot_id, acq_time, event_id, method, distance_m, dt_hours, score)
    SELECT DISTINCT ON (c.hotspot_id, c.acq_time)
           c.hotspot_id, c.acq_time, c.event_id, c.method, c.distance_m, c.dt_hours, c.score
    FROM (
        -- 1.0: inside a perimeter of this fire mapped within 12 h of the detection
        SELECT h.hotspot_id, h.acq_time, o.event_id, 'inside_perimeter_same_time' AS method, 0::real AS distance_m,
               min(abs(extract(epoch FROM h.acq_time - o.observed_at)) / 3600.0)::real AS dt_hours, 1.0::real AS score
        FROM _h h
        JOIN _pobs o ON ST_Intersects(o.g, h.geom)
                    AND o.observed_at BETWEEN h.acq_time - interval '12 hours' AND h.acq_time + interval '12 hours'
        JOIN _ev ev ON ev.event_id = o.event_id AND h.acq_time <@ ev.win
        GROUP BY h.hotspot_id, h.acq_time, o.event_id
        UNION ALL
        -- 0.8 inside the final perimeter, else <= 0.6 decaying with distance (to the outline or the point of origin)
        SELECT d.hotspot_id, d.acq_time, d.event_id,
               CASE WHEN d.dist = 0 AND d.has_perimeter THEN 'inside_final_perimeter' ELSE 'buffer' END,
               d.dist,
               (extract(epoch FROM d.acq_time - coalesce(d.discovered_at, lower(d.win) + interval '1 day')) / 3600.0)::real,
               CASE WHEN d.dist = 0 AND d.has_perimeter THEN 0.8 ELSE 0.6 * (1 - d.dist / p_buffer_m) END::real
        FROM (
            SELECT h.hotspot_id, h.acq_time, ev.event_id, ev.has_perimeter, ev.discovered_at, ev.win,
                   min(ST_Distance(h.geom::geography, p.g::geography))::real AS dist
            FROM _h h
            JOIN _piece p ON ST_DWithin(p.g, h.geom, p_buffer_m / (111320.0 * cos(radians(least(abs(h.latitude), 80)))))
            JOIN _ev ev ON ev.event_id = p.event_id AND h.acq_time <@ ev.win
            GROUP BY h.hotspot_id, h.acq_time, ev.event_id, ev.has_perimeter, ev.discovered_at, ev.win
        ) d
        WHERE d.dist <= p_buffer_m
    ) c
    ORDER BY c.hotspot_id, c.acq_time, c.score DESC, c.distance_m, abs(c.dt_hours);

    -- 0.4: leftover detections that cluster (DBSCAN, ~1.5 km, >= 3 per day) within 5 km of a point of origin
    INSERT INTO hotspot_event_link (hotspot_id, acq_time, event_id, method, distance_m, dt_hours, score)
    SELECT DISTINCT ON (u.hotspot_id, u.acq_time)
           u.hotspot_id, u.acq_time, m.event_id, 'cluster',
           ST_Distance(u.geom::geography, m.poo::geography)::real,
           (extract(epoch FROM u.acq_time - m.discovered_at) / 3600.0)::real, 0.4
    FROM (
        SELECT h.*, ST_ClusterDBSCAN(h.geom, eps := 0.015, minpoints := 3) OVER (PARTITION BY date_trunc('day', h.acq_time)) AS cid
        FROM _h h
        WHERE NOT EXISTS (SELECT 1 FROM hotspot_event_link l WHERE l.hotspot_id = h.hotspot_id AND l.acq_time = h.acq_time)
    ) u
    JOIN LATERAL (
        SELECT ev.event_id, ev.poo, ev.discovered_at
        FROM _ev ev
        WHERE u.cid IS NOT NULL AND ev.poo IS NOT NULL AND u.acq_time <@ ev.win
          AND ST_DWithin(u.geom, ev.poo, 5000 / (111320.0 * cos(radians(least(abs(u.latitude), 80)))))
          AND ST_DWithin(u.geom::geography, ev.poo::geography, 5000)
        ORDER BY ST_Distance(u.geom::geography, ev.poo::geography)
        LIMIT 1
    ) m ON true
    ORDER BY u.hotspot_id, u.acq_time;

    RETURN QUERY
        SELECT l.method, count(*) FROM hotspot_event_link l
        WHERE l.acq_time >= p_from AND l.acq_time < p_to GROUP BY l.method ORDER BY 1;
END $$;

-- Groups a fire's linked detections into satellite overpasses: same sensor, gaps under 15 minutes.
CREATE OR REPLACE VIEW vw_overpass AS
WITH d AS (
    SELECT l.event_id, h.sensor_id, h.acq_time, h.geom, h.frp_mw, h.confidence_class,
           CASE WHEN h.acq_time - lag(h.acq_time) OVER w > interval '15 minutes' OR lag(h.acq_time) OVER w IS NULL
                THEN 1 ELSE 0 END AS new_pass
    FROM hotspot_event_link l
    JOIN hotspot h USING (hotspot_id, acq_time)
    WINDOW w AS (PARTITION BY l.event_id, h.sensor_id ORDER BY h.acq_time)
), g AS (
    SELECT *, sum(new_pass) OVER (PARTITION BY event_id, sensor_id ORDER BY acq_time) AS pass_no FROM d
)
SELECT event_id, sensor_id, pass_no,
       min(acq_time) AS pass_start, max(acq_time) AS pass_end,
       count(*) AS detections,
       count(*) FILTER (WHERE confidence_class = 'high') AS high_conf,
       sum(frp_mw) AS frp_total_mw,
       ST_Collect(geom) AS points
FROM g
GROUP BY event_id, sensor_id, pass_no;

-- Estimated ignition: centroid of the first overpass that saw the fire with nominal or high confidence.
CREATE OR REPLACE FUNCTION estimate_ignition(p_event bigint)
RETURNS fire_event LANGUAGE sql AS $$
    WITH first_seen AS (
        SELECT min(h.acq_time) AS t
        FROM hotspot_event_link l JOIN hotspot h USING (hotspot_id, acq_time)
        WHERE l.event_id = p_event AND h.confidence_class IN ('nominal', 'high') AND l.score >= 0.6
    ), first_pass AS (
        SELECT h.geom, h.acq_time
        FROM hotspot_event_link l JOIN hotspot h USING (hotspot_id, acq_time), first_seen f
        WHERE l.event_id = p_event AND h.confidence_class IN ('nominal', 'high') AND l.score >= 0.6
          AND h.acq_time BETWEEN f.t AND f.t + interval '15 minutes'
    )
    UPDATE fire_event e
       SET ignition_est = (SELECT ST_Centroid(ST_Collect(geom)) FROM first_pass),
           ignition_est_time = (SELECT t FROM first_seen)
     WHERE e.event_id = p_event AND EXISTS (SELECT 1 FROM first_pass)
    RETURNING e.*
$$;

-- Perimeters derived from cumulative VIIRS detections, one per overpass, so fires without mapped
-- perimeters still get a progression series. Each footprint is a 375 m pixel (half-width 190 m).
CREATE OR REPLACE FUNCTION derive_viirs_perimeters(p_event bigint, p_concavity real DEFAULT 0.3)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    DELETE FROM perimeter_obs WHERE event_id = p_event AND source = 'VIIRS_DERIVED';
    WITH passes AS (
        SELECT DISTINCT o.pass_end AS t
        FROM vw_overpass o JOIN sensor s USING (sensor_id)
        WHERE o.event_id = p_event AND s.instrument = 'VIIRS'
    ), cum AS (
        SELECT p.t,
               ST_Collect(h.geom) AS pts
        FROM passes p
        JOIN hotspot_event_link l ON l.event_id = p_event AND l.acq_time <= p.t AND l.score >= 0.6
        JOIN hotspot h USING (hotspot_id, acq_time)
        JOIN sensor s ON s.sensor_id = h.sensor_id AND s.instrument = 'VIIRS'
        GROUP BY p.t
    )
    INSERT INTO perimeter_obs (event_id, observed_at, source, source_id, map_method, geom)
    SELECT p_event, t, 'VIIRS_DERIVED', p_event || '@' || to_char(t, 'YYYYMMDD"T"HH24MISS'),
           format('concave hull (%s) of cumulative VIIRS 375 m detections, buffered 190 m', p_concavity),
           ST_Multi(ST_CollectionExtract(ST_MakeValid(
               ST_Buffer(ST_ConcaveHull(pts, p_concavity)::geography, 190)::geometry), 3))
    FROM cum
    WHERE ST_NumGeometries(pts) >= 1;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

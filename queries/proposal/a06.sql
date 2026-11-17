-- Advanced query 6 (from the project proposal)
-- For each year in the database, what are the names, state locations, and maximum brightness temperatures of the incidents that recorded the absolute hottest single hotspot for that specific year?
WITH linked AS (
    SELECT extract(year FROM h.acq_time)::int AS year, e.name, st.usps AS state, h.brightness_k, s.code AS sensor,
           rank() OVER (PARTITION BY extract(year FROM h.acq_time) ORDER BY h.brightness_k DESC) AS rk
    FROM hotspot_event_link l
    JOIN hotspot h USING (hotspot_id, acq_time)
    JOIN sensor s ON s.sensor_id = h.sensor_id
    JOIN fire_event e ON e.event_id = l.event_id
    LEFT JOIN us_state st ON st.code = e.state_code
    WHERE h.brightness_k IS NOT NULL
)
SELECT year, name, state, brightness_k AS max_brightness_k, sensor
FROM linked WHERE rk = 1
ORDER BY year;

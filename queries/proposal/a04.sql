-- Advanced query 4 (from the project proposal)
-- Which state had the highest average thermal hotspot brightness temperature across all years recorded in the database?
-- Wildfire detections only: FIRMS type 0 (vegetation fire). Volcanoes (e.g. Kilauea), industrial heat and offshore flares are excluded.
-- Averaged per instrument family, because MODIS and VIIRS fire channels are not on the same scale.
SELECT s.instrument, st.usps AS state, round(avg(h.brightness_k)::numeric, 2) AS avg_brightness_k, count(*) AS detections
FROM mv_hotspot_state m
JOIN hotspot h USING (hotspot_id, acq_time)
JOIN sensor s ON s.sensor_id = h.sensor_id
JOIN us_state st ON st.code = m.state_code
WHERE coalesce(h.fire_type, 0) = 0
GROUP BY s.instrument, st.usps
HAVING count(*) >= 1000
ORDER BY s.instrument, avg_brightness_k DESC;
-- the top row of each instrument answers the question

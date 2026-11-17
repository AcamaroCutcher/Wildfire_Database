-- Advanced query 3 (from the project proposal)
-- Which state had the highest total count of thermal hotspots recorded specifically by the MODIS satellite in the year 2022?
-- Wildfire detections only: FIRMS type 0 (vegetation fire). Volcanoes (e.g. Kilauea), industrial heat and offshore flares are excluded.
SELECT st.usps AS state, count(*) AS modis_hotspots
FROM mv_hotspot_state m
JOIN hotspot h USING (hotspot_id, acq_time)
JOIN sensor s ON s.sensor_id = m.sensor_id
JOIN us_state st ON st.code = m.state_code
WHERE s.instrument = 'MODIS' AND coalesce(h.fire_type, 0) = 0 AND m.acq_time >= '2022-01-01' AND m.acq_time < '2023-01-01'
GROUP BY st.usps
ORDER BY modis_hotspots DESC
LIMIT 1;

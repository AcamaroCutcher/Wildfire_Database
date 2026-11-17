-- Basic query 2 (from the project proposal)
-- What are the latitude, longitude, and brightness of the top 10 brightest thermal hotspots recorded in the year 2024?
-- brightness_k is the fire-channel brightness temperature (MODIS band 21/22, VIIRS I4).
-- The instruments saturate differently, so the sensor is shown alongside.
SELECT h.latitude, h.longitude, h.brightness_k, s.code AS sensor, h.acq_time
FROM hotspot h JOIN sensor s USING (sensor_id)
WHERE h.acq_time >= '2024-01-01' AND h.acq_time < '2025-01-01'
  AND h.brightness_k IS NOT NULL
  AND coalesce(h.fire_type, 0) = 0      -- wildfire detections only (no volcanoes or industrial heat)
ORDER BY h.brightness_k DESC
LIMIT 10;

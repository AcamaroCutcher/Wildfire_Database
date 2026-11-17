-- Advanced query 9 (from the project proposal)
-- For the incident named 'Palisades', what is the total count of high-confidence (greater than 80%) hotspots detected by each distinct satellite instrument type?
-- MODIS: confidence_pct > 80. VIIRS has no percentage; its 'high' class is used and labelled.
WITH fire AS (
    SELECT event_id FROM fire_event
    WHERE lower(name) = 'palisades' AND incident_type = 'WF'
    ORDER BY coalesce(reported_acres, final_acres) DESC NULLS LAST
    LIMIT 1                                           -- the largest fire of that name (Jan 2025, Los Angeles)
)
SELECT s.platform, s.instrument,
       count(*) FILTER (WHERE h.confidence_pct > 80 OR (h.confidence_pct IS NULL AND h.confidence_class = 'high')) AS high_confidence,
       count(*) AS all_detections
FROM fire JOIN hotspot_event_link l USING (event_id)
JOIN hotspot h USING (hotspot_id, acq_time)
JOIN sensor s ON s.sensor_id = h.sensor_id
GROUP BY s.platform, s.instrument
ORDER BY high_confidence DESC;

-- Advanced query 2 (from the project proposal)
-- Which single state recorded the highest overall average satellite hotspot confidence level, and what was that average percentage?
-- A percentage average only exists for MODIS. For all sensors together, the share of high-confidence
-- detections is the comparable measure, so both are reported. (REFRESH MATERIALIZED VIEW mv_hotspot_state first.)
-- Wildfire detections only: FIRMS type 0 (vegetation fire). Volcanoes (e.g. Kilauea), industrial heat and offshore flares are excluded.
SELECT st.usps AS state,
       round(avg(h.confidence_pct), 1)                                   AS avg_modis_confidence_pct,
       round(100.0 * avg((h.confidence_class = 'high')::int), 1)         AS pct_high_all_sensors,
       count(*)                                                          AS detections
FROM mv_hotspot_state m
JOIN hotspot h USING (hotspot_id, acq_time)
JOIN us_state st ON st.code = m.state_code
WHERE coalesce(h.fire_type, 0) = 0
GROUP BY st.usps
HAVING count(h.confidence_pct) >= 100
ORDER BY avg_modis_confidence_pct DESC
LIMIT 1;

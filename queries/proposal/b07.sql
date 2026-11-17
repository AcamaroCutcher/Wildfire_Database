-- Basic query 7 (from the project proposal)
-- List the distinct names of all incidents in 2022 that had at least one recorded satellite hotspot with a confidence level strictly greater than 90%.
-- Only MODIS reports a percentage. VIIRS reports low / nominal / high, so for VIIRS "high" is the
-- closest equivalent (FIRMS: high = no ambiguity in the fire test). Both are included and labelled.
SELECT e.name, e.unique_fire_identifier,
       count(*)                                                                   AS qualifying_detections,
       coalesce(bool_or(h.confidence_pct > 90), false)                                AS modis_over_90,
       coalesce(bool_or(h.confidence_pct IS NULL AND h.confidence_class = 'high'), false) AS viirs_high
FROM fire_event e
JOIN hotspot_event_link l USING (event_id)
JOIN hotspot h USING (hotspot_id, acq_time)
WHERE e.discovered_at >= '2022-01-01' AND e.discovered_at < '2023-01-01'
  AND (h.confidence_pct > 90 OR (h.confidence_pct IS NULL AND h.confidence_class = 'high'))
GROUP BY e.event_id, e.name, e.unique_fire_identifier
ORDER BY qualifying_detections DESC;

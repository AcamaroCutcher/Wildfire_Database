-- Basic query 5 (from the project proposal)
-- Which specific incident names had daily perimeters recorded on more than 10 distinct days?
SELECT e.name, e.unique_fire_identifier, count(DISTINCT p.observed_at::date) AS mapped_days
FROM perimeter_obs p JOIN fire_event e USING (event_id)
WHERE p.source = 'WFIGS' AND NOT p.is_final
GROUP BY e.event_id, e.name, e.unique_fire_identifier
HAVING count(DISTINCT p.observed_at::date) > 10
ORDER BY mapped_days DESC;

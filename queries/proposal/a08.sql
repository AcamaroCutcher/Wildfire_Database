-- Advanced query 8 (from the project proposal)
-- Which specific incident names had daily perimeters recorded on more than 10 distinct days, and what was the average recorded acreage across those mapped days?
WITH daily AS (
    SELECT p.event_id, p.observed_at::date AS day, max(p.acres) AS acres   -- largest perimeter mapped that day
    FROM perimeter_obs p
    WHERE p.source = 'WFIGS' AND NOT p.is_final
    GROUP BY p.event_id, p.observed_at::date
)
SELECT e.name, count(*) AS mapped_days, round(avg(d.acres)) AS avg_mapped_acres
FROM daily d JOIN fire_event e USING (event_id)
GROUP BY e.event_id, e.name
HAVING count(*) > 10
ORDER BY mapped_days DESC;

-- Basic query 8 (from the project proposal)
-- List the names and discovery dates of all incidents located in Oregon that were classified with a 'Type 1' Incident Complexity Level.
SELECT incident_name, discovered_at::date
FROM vw_wildfire_historical_record
WHERE state = 'OR' AND complexity_level = 1
ORDER BY discovered_at;

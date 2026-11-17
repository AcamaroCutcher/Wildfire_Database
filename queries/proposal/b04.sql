-- Basic query 4 (from the project proposal)
-- List the names of all incidents from 2023, sorted chronologically by their recorded discovery date.
SELECT incident_name, discovered_at
FROM vw_wildfire_historical_record
WHERE discovered_at >= '2023-01-01' AND discovered_at < '2024-01-01'
ORDER BY discovered_at
LIMIT 100;   -- tens of thousands of 2023 incidents; remove LIMIT for all

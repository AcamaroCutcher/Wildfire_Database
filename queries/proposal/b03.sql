-- Basic query 3 (from the project proposal)
-- List the names and total acres burned for all incidents discovered in 2021 that exceeded 10,000 acres.
SELECT incident_name, round(acres_burned) AS acres_burned, discovered_at::date
FROM vw_wildfire_historical_record
WHERE discovered_at >= '2021-01-01' AND discovered_at < '2022-01-01'
  AND acres_burned > 10000
ORDER BY acres_burned DESC;

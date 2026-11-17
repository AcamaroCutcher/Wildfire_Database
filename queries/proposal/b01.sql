-- Basic query 1 (from the project proposal)
-- List the names and total recorded acres burned for all incidents, sorted from largest to smallest.
SELECT incident_name, round(acres_burned) AS acres_burned
FROM vw_wildfire_historical_record
WHERE acres_burned IS NOT NULL
ORDER BY acres_burned DESC
LIMIT 50;   -- remove LIMIT for the full list

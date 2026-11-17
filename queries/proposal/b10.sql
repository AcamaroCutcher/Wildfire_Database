-- Basic query 10 (from the project proposal)
-- List the names of all incidents in California, strictly excluding any that are categorized as a 'Prescribed Fire'.
SELECT incident_name, discovered_at::date, fire_type
FROM vw_wildfire_historical_record
WHERE state = 'CA' AND incident_type IS DISTINCT FROM 'RX'
ORDER BY discovered_at DESC
LIMIT 100;   -- remove LIMIT for all

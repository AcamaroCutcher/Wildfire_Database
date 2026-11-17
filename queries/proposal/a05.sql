-- Advanced query 5 (from the project proposal)
-- List the county name and the total number of incidents it experienced in the year 2023, sorted to show the county with the most incidents at the top.
SELECT county, state, count(*) AS incidents
FROM vw_wildfire_historical_record
WHERE discovered_at >= '2023-01-01' AND discovered_at < '2024-01-01' AND county IS NOT NULL
GROUP BY county, state
ORDER BY incidents DESC
LIMIT 25;

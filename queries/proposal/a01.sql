-- Advanced query 1 (from the project proposal)
-- List the state, the year, and the average total acres burned for incidents in that state, grouped by both state and year, sorted from highest average acreage to lowest.
SELECT state, extract(year FROM discovered_at)::int AS year, round(avg(acres_burned), 1) AS avg_acres, count(*) AS incidents
FROM vw_wildfire_historical_record
WHERE state IS NOT NULL AND acres_burned IS NOT NULL AND discovered_at IS NOT NULL
GROUP BY state, year
ORDER BY avg_acres DESC;

-- Advanced query 10 (from the project proposal)
-- What is the average total acres burned per incident, grouped by each distinct Incident Complexity Level (Type 1 through Type 5)?
SELECT complexity_level, complexity_code, round(avg(acres_burned), 1) AS avg_acres, count(*) AS incidents
FROM vw_wildfire_historical_record
WHERE complexity_level IS NOT NULL AND acres_burned IS NOT NULL
GROUP BY complexity_level, complexity_code
ORDER BY complexity_level;

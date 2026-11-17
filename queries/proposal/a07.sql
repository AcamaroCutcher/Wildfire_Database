-- Advanced query 7 (from the project proposal)
-- Create a view named vw_wildfire_historical_record that joins the Incidents, Fire_Types, and Jurisdictions tables. Using only this view, list the state, incident name, acres burned, and discovery date for all "Wildfires" (excluding "Prescribed Fires") that burned more than 10,000 acres, sorted by state, then by acres burned (descending), and finally by date.
-- The view is defined in schema/70_views.sql (fire_event + lk_incident_type + state/county/agency).
SELECT state, incident_name, round(acres_burned) AS acres_burned, discovered_at::date
FROM vw_wildfire_historical_record
WHERE fire_type = 'Wildfire' AND acres_burned > 10000
ORDER BY state, acres_burned DESC, discovered_at;

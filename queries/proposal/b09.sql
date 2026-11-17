-- Basic query 9 (from the project proposal)
-- List all unique incident type categories present in the database without any duplicates.
SELECT DISTINCT e.incident_type, t.description
FROM fire_event e JOIN lk_incident_type t ON t.code = e.incident_type
ORDER BY 1;

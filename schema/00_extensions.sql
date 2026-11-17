-- Wildfire–satellite database
-- PostgreSQL 16+ with PostGIS 3.4+ (tested on PostgreSQL 17 / PostGIS 3.5).
-- All geometry is stored in WGS 84 (EPSG:4326); distances and areas use the geography type.
-- All times are timestamptz and are written in UTC.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- mixed GiST indexes (time + geometry)

SET TIME ZONE 'UTC';

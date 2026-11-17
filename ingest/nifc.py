"""NIFC incidents (InFORM Fire Occurrence) and perimeters (WFIGS) -> fire_event, perimeter_obs.

  python nifc.py incidents [--csv path/to/inform_fire_occurrence_data.csv] [--since 2020]
  python nifc.py perimeters [--since 2020]

Incidents: read from a downloaded InFORM CSV if you have one (fast), otherwise from the
InFORM_FireOccurrence_Public feature service. Perimeters always come from the services, with geometry:
  WFIGS_Interagency_Perimeters      one (final or latest) perimeter per incident, 2021 onward
  WFIGS_Daily_Perimeters_Public     dated daily perimeters: the progression record
InFORM date/times are stored by ArcGIS in UTC; they are loaded as UTC.
"""
import argparse
import io
import os

import pandas as pd

from common import arcgis_fetch_geojson, connect, ogr_to_pg, run_sql

COLS = {
    'UniqueFireIdentifier': 'ufi', 'Incident Name': 'name', 'Incident Type Category': 'itype',
    'Fire Discovery Date Time': 'discovered', 'Containment Date Time': 'contained', 'Control Date Time': 'controlled',
    'FireOutDateTime': 'out', 'Incident Size': 'size', 'Final Acres': 'final_acres', 'Fire Mgmt Complexity': 'complexity',
    'Estimated Cost To Date': 'cost', 'Predominant Fuel Group': 'fuel_group', 'Predominant Fuel Model': 'fuel_model',
    'Fire Behavior General': 'behavior', 'Fire Cause General': 'cause', 'POO State': 'state', 'POO County': 'county',
    'POO Fips': 'fips', 'POO Jurisdictional Agency': 'agency', 'Initial Latitude': 'lat', 'Initial Longitude': 'lon',
    'x': 'x', 'y': 'y', 'Modified On Date Time': 'modified',
}

TS = "pg_temp.ts({c})"   # session is UTC; placeholder dates (1970, far future) become NULL


def load_incidents_csv(conn, path, since):
    run_sql(conn, 'DROP TABLE IF EXISTS stg_inform; CREATE UNLOGGED TABLE stg_inform (%s)' %
            ', '.join(f'{v} text' for v in COLS.values()))
    n = 0
    for chunk in pd.read_csv(path, usecols=list(COLS), dtype=str, encoding='utf-8-sig', chunksize=200_000):
        chunk = chunk.rename(columns=COLS)[list(COLS.values())]
        buf = io.StringIO()
        chunk.to_csv(buf, index=False, header=False)
        buf.seek(0)
        with conn.cursor() as cur, cur.copy('COPY stg_inform FROM STDIN (FORMAT csv)') as cp:
            cp.write(buf.read())
        n += len(chunk)
    print(f'  staged {n:,} InFORM rows')
    upsert_incidents(conn, since)


def upsert_incidents(conn, since):
    run_sql(conn, """
        CREATE OR REPLACE FUNCTION pg_temp.ts(t text) RETURNS timestamptz LANGUAGE sql STABLE AS $$
            SELECT CASE WHEN v >= '1990-01-01' AND v < now() + interval '1 year' THEN v END
            FROM (SELECT to_timestamp(nullif(t, ''), 'FMMM/FMDD/YYYY FMHH12:MI:SS AM') AS v) q
        $$;
        -- an end time before discovery is a data-entry error: drop it rather than the whole incident
        CREATE OR REPLACE FUNCTION pg_temp.after(t timestamptz, start timestamptz) RETURNS timestamptz LANGUAGE sql IMMUTABLE AS $$
            SELECT CASE WHEN start IS NULL OR t >= start - interval '1 day' THEN t END
        $$;
    """)
    run_sql(conn, f"""
        -- agencies and complexity values seen in the data
        INSERT INTO agency (code) SELECT DISTINCT agency FROM stg_inform WHERE nullif(agency, '') IS NOT NULL ON CONFLICT DO NOTHING;
        INSERT INTO lk_complexity (code, level)
        SELECT DISTINCT complexity, substring(complexity FROM 'Type ([1-5])')::smallint FROM stg_inform
        WHERE nullif(complexity, '') IS NOT NULL ON CONFLICT DO NOTHING;
        INSERT INTO lk_fuel_group (code) SELECT DISTINCT fuel_group FROM stg_inform
        WHERE nullif(fuel_group, '') IS NOT NULL ON CONFLICT DO NOTHING;

        INSERT INTO fire_event (unique_fire_identifier, name, incident_type, cause_code, complexity_code, fuel_group_code,
                                fuel_model, fire_behavior, state_code, county_geoid, county_name, agency_code, poo,
                                discovered_at, contained_at, controlled_at, out_at, reported_acres, estimated_cost_usd, source_updated_at)
        SELECT DISTINCT ON (s.ufi)
               s.ufi, coalesce(nullif(trim(s.name), ''), 'Unnamed'),
               (SELECT code FROM lk_incident_type WHERE code = s.itype),
               (SELECT code FROM lk_cause WHERE code = split_part(s.cause, '.', 1)),
               nullif(s.complexity, ''), nullif(s.fuel_group, ''), nullif(s.fuel_model, ''), nullif(s.behavior, ''),
               (SELECT code FROM us_state WHERE code = s.state),
               (SELECT geoid FROM us_county WHERE geoid = lpad(nullif(s.fips, ''), 5, '0')),
               nullif(s.county, ''), nullif(s.agency, ''),
               CASE WHEN nullif(s.lat, '') IS NOT NULL AND nullif(s.lon, '') IS NOT NULL
                         AND s.lat::float8 BETWEEN -90 AND 90 AND s.lon::float8 BETWEEN -180 AND 180 AND s.lat::float8 <> 0
                    THEN ST_SetSRID(ST_MakePoint(s.lon::float8, s.lat::float8), 4326)
                    WHEN nullif(s.x, '') IS NOT NULL
                    THEN ST_Transform(ST_SetSRID(ST_MakePoint(s.x::float8, s.y::float8), 3857), 4326) END,
               {TS.format(c='s.discovered')}, pg_temp.after({TS.format(c='s.contained')}, {TS.format(c='s.discovered')}),
               pg_temp.after({TS.format(c='s.controlled')}, {TS.format(c='s.discovered')}), pg_temp.after({TS.format(c='s.out')}, {TS.format(c='s.discovered')}),
               coalesce(nullif(s.final_acres, '')::numeric, nullif(s.size, '')::numeric),
               nullif(s.cost, '')::numeric, {TS.format(c='s.modified')}
        FROM stg_inform s
        WHERE nullif(s.ufi, '') IS NOT NULL
          AND {TS.format(c='s.discovered')} >= make_timestamptz({since}, 1, 1, 0, 0, 0, 'UTC')
        ORDER BY s.ufi, {TS.format(c='s.modified')} DESC NULLS LAST
        ON CONFLICT (unique_fire_identifier) DO UPDATE SET
            name = EXCLUDED.name, incident_type = EXCLUDED.incident_type, cause_code = EXCLUDED.cause_code,
            complexity_code = EXCLUDED.complexity_code, fuel_group_code = EXCLUDED.fuel_group_code,
            state_code = EXCLUDED.state_code, county_geoid = EXCLUDED.county_geoid, county_name = EXCLUDED.county_name,
            agency_code = EXCLUDED.agency_code, poo = EXCLUDED.poo, discovered_at = EXCLUDED.discovered_at,
            contained_at = EXCLUDED.contained_at, controlled_at = EXCLUDED.controlled_at, out_at = EXCLUDED.out_at,
            reported_acres = EXCLUDED.reported_acres, estimated_cost_usd = EXCLUDED.estimated_cost_usd,
            source_updated_at = EXCLUDED.source_updated_at;
        DROP TABLE stg_inform;
    """)
    print('  fire_event rows:', run_sql(conn, 'SELECT count(*) FROM fire_event')[0][0])


PERIM_FIELDS = ('poly_IncidentName,poly_MapMethod,poly_GISAcres,poly_PolygonDateTime,poly_DateCurrent,poly_IRWINID,'
                'attr_UniqueFireIdentifier,attr_IrwinID,attr_IncidentName,attr_FireDiscoveryDateTime,attr_ContainmentDateTime,'
                'attr_FireOutDateTime,attr_IncidentTypeCategory,attr_POOState,attr_InitialLatitude,attr_InitialLongitude,'
                'attr_IncidentSize,attr_FinalAcres,GlobalID')


def load_perimeters(conn, since):
    where = f"attr_FireDiscoveryDateTime >= DATE '{since}-01-01'"
    print('  fetching WFIGS final/latest perimeters …')
    final = arcgis_fetch_geojson('WFIGS_Interagency_Perimeters', f'wfigs_final_{since}', where, PERIM_FIELDS)
    print('  fetching WFIGS daily perimeters …')
    daily = arcgis_fetch_geojson('WFIGS_Daily_Perimeters_Public', f'wfigs_daily_{since}', where, PERIM_FIELDS + ',BurnPeriod')
    ogr_to_pg(str(final), 'stg_perim_final')
    ogr_to_pg(str(daily), 'stg_perim_daily')
    for t in ('stg_perim_final', 'stg_perim_daily'):
        for c in ('poly_polygondatetime', 'poly_datecurrent', 'attr_firediscoverydatetime', 'attr_containmentdatetime', 'attr_fireoutdatetime'):
            typ = run_sql(conn, "SELECT data_type FROM information_schema.columns WHERE table_name = %s AND column_name = %s", (t, c))
            if not typ:
                run_sql(conn, f'ALTER TABLE {t} ADD COLUMN {c} timestamptz'); continue
            if typ[0][0] in ('bigint', 'integer', 'numeric', 'double precision'):     # ArcGIS GeoJSON dates are epoch ms
                run_sql(conn, f"""ALTER TABLE {t} ALTER COLUMN {c} TYPE timestamptz USING
                                 CASE WHEN {c} IS NULL OR {c} < 631152000000 THEN NULL ELSE to_timestamp({c} / 1000.0) END""")
            elif typ[0][0].startswith('timestamp'):
                run_sql(conn, f"UPDATE {t} SET {c} = NULL WHERE {c} < '1990-01-01'")
    print('  staged perimeters:', run_sql(conn, 'SELECT (SELECT count(*) FROM stg_perim_final), (SELECT count(*) FROM stg_perim_daily)')[0])
    run_sql(conn, """
        -- fires that have perimeters but were not in the incident table (e.g. other years or sources)
        INSERT INTO fire_event (unique_fire_identifier, name, incident_type, state_code, poo, discovered_at, contained_at, out_at, reported_acres)
        SELECT DISTINCT ON (attr_uniquefireidentifier) attr_uniquefireidentifier,
               coalesce(attr_incidentname, poly_incidentname, 'Unnamed'),
               (SELECT code FROM lk_incident_type WHERE code = attr_incidenttypecategory),
               (SELECT code FROM us_state WHERE code = attr_poostate),
               CASE WHEN attr_initiallatitude IS NOT NULL THEN ST_SetSRID(ST_MakePoint(attr_initiallongitude, attr_initiallatitude), 4326) END,
               attr_firediscoverydatetime, attr_containmentdatetime, attr_fireoutdatetime,
               coalesce(attr_finalacres, attr_incidentsize)
        FROM (SELECT attr_uniquefireidentifier, attr_incidentname, poly_incidentname, attr_incidenttypecategory, attr_poostate, attr_initiallatitude, attr_initiallongitude, attr_firediscoverydatetime, attr_containmentdatetime, attr_fireoutdatetime, attr_finalacres, attr_incidentsize FROM stg_perim_final UNION ALL SELECT attr_uniquefireidentifier, attr_incidentname, poly_incidentname, attr_incidenttypecategory, attr_poostate, attr_initiallatitude, attr_initiallongitude, attr_firediscoverydatetime, attr_containmentdatetime, attr_fireoutdatetime, attr_finalacres, attr_incidentsize FROM stg_perim_daily) z
        WHERE attr_uniquefireidentifier IS NOT NULL
        ON CONFLICT (unique_fire_identifier) DO NOTHING;
    """)
    run_sql(conn, """
        UPDATE fire_event e SET irwin_id = s.irwin::uuid
        FROM (SELECT DISTINCT ON (attr_uniquefireidentifier) attr_uniquefireidentifier AS ufi,
                     trim(both '{}' FROM coalesce(attr_irwinid, poly_irwinid)) AS irwin
              FROM stg_perim_final WHERE coalesce(attr_irwinid, poly_irwinid) IS NOT NULL) s
        WHERE e.unique_fire_identifier = s.ufi AND e.irwin_id IS NULL
          AND s.irwin ~* '^[0-9a-f-]{36}$'
          AND NOT EXISTS (SELECT 1 FROM fire_event x WHERE x.irwin_id = s.irwin::uuid);

        INSERT INTO perimeter_obs (event_id, observed_at, source, source_id, map_method, is_final, reported_acres, geom)
        SELECT e.event_id, coalesce(p.poly_polygondatetime, p.poly_datecurrent), 'WFIGS', 'final:' || p.globalid,
               p.poly_mapmethod, true, p.poly_gisacres, ST_Multi(ST_CollectionExtract(ST_MakeValid(p.geom), 3))
        FROM stg_perim_final p JOIN fire_event e ON e.unique_fire_identifier = p.attr_uniquefireidentifier
        WHERE p.geom IS NOT NULL AND coalesce(p.poly_polygondatetime, p.poly_datecurrent) IS NOT NULL
          AND NOT ST_IsEmpty(ST_CollectionExtract(ST_MakeValid(p.geom), 3))
        ON CONFLICT (source, source_id) DO UPDATE SET geom = EXCLUDED.geom, observed_at = EXCLUDED.observed_at;

        INSERT INTO perimeter_obs (event_id, observed_at, source, source_id, map_method, is_final, reported_acres, geom)
        SELECT e.event_id, coalesce(p.poly_polygondatetime, p.poly_datecurrent), 'WFIGS', 'daily:' || p.globalid,
               p.poly_mapmethod, false, p.poly_gisacres, ST_Multi(ST_CollectionExtract(ST_MakeValid(p.geom), 3))
        FROM stg_perim_daily p JOIN fire_event e ON e.unique_fire_identifier = p.attr_uniquefireidentifier
        WHERE p.geom IS NOT NULL AND coalesce(p.poly_polygondatetime, p.poly_datecurrent) IS NOT NULL
          AND NOT ST_IsEmpty(ST_CollectionExtract(ST_MakeValid(p.geom), 3))
        ON CONFLICT (source, source_id) DO UPDATE SET geom = EXCLUDED.geom, observed_at = EXCLUDED.observed_at;

        -- final perimeter on the event: the WFIGS final polygon (MTBS can override later)
        UPDATE fire_event e SET final_perimeter = p.geom, final_perimeter_source = 'WFIGS'
        FROM (SELECT DISTINCT ON (event_id) event_id, geom FROM perimeter_obs
              WHERE source = 'WFIGS' AND is_final ORDER BY event_id, observed_at DESC) p
        WHERE e.event_id = p.event_id;
        DROP TABLE stg_perim_final; DROP TABLE stg_perim_daily;
    """)
    print('  perimeter_obs rows:', run_sql(conn, 'SELECT count(*), count(DISTINCT event_id) FROM perimeter_obs')[0])


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=['incidents', 'perimeters', 'all'])
    ap.add_argument('--csv', default=os.path.expanduser('~/Documents/Wildfire_Data/inform_fire_occurrence_data.csv'))
    ap.add_argument('--since', type=int, default=2020)
    a = ap.parse_args()
    with connect() as conn:
        if a.what in ('incidents', 'all'):
            if os.path.exists(a.csv):
                load_incidents_csv(conn, a.csv, a.since)
            else:
                raise SystemExit('InFORM CSV not found; download it from NIFC Open Data (InFORM Fire Occurrence) and pass --csv')
        if a.what in ('perimeters', 'all'):
            load_perimeters(conn, a.since)
        conn.commit()

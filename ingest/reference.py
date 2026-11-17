"""Reference data: US states and counties (Census cartographic boundaries) and NIFC code lists.

Code lists come from the coded-value domains of NIFC's InFORM Fire Occurrence layer, so every code
is stored with its description (the old database had bare numbers such as cause '13').
"""
from common import NIFC, cached, connect, get_json, ogr_to_pg, run_sql

CENSUS = 'https://www2.census.gov/geo/tiger/GENZ2024/shp'


def load_states_counties(conn):
    st = cached(f'{CENSUS}/cb_2024_us_state_500k.zip', 'cb_2024_us_state_500k.zip')
    co = cached(f'{CENSUS}/cb_2024_us_county_500k.zip', 'cb_2024_us_county_500k.zip')
    ogr_to_pg(f'/vsizip/{st}', 'stg_state')
    ogr_to_pg(f'/vsizip/{co}', 'stg_county')
    run_sql(conn, """
        INSERT INTO us_state (code, usps, name, geom)
        SELECT 'US-' || stusps, stusps, name, ST_Multi(geom) FROM stg_state
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, geom = EXCLUDED.geom;
        INSERT INTO us_county (geoid, state_code, name, geom)
        SELECT c.geoid, 'US-' || c.stusps, c.name, ST_Multi(c.geom) FROM stg_county c
        WHERE EXISTS (SELECT 1 FROM us_state s WHERE s.code = 'US-' || c.stusps)
        ON CONFLICT (geoid) DO UPDATE SET name = EXCLUDED.name, geom = EXCLUDED.geom;
        DROP TABLE stg_state; DROP TABLE stg_county;
    """)
    print('  states:', run_sql(conn, 'SELECT count(*) FROM us_state')[0][0],
          'counties:', run_sql(conn, 'SELECT count(*) FROM us_county')[0][0])


def load_domains(conn):
    meta = get_json(f'{NIFC}/InFORM_FireOccurrence_Public/FeatureServer/0?f=json')
    dom = {f['name']: {c['code']: c['name'] for c in f['domain']['codedValues']}
           for f in meta['fields'] if f.get('domain') and f['domain'].get('codedValues')}
    general = dom['FireCauseGeneralID']
    cause_class = {'1': 'Natural', '2': 'Undetermined'}          # general 1 and 2 are their own class
    with conn.cursor() as cur:
        for code, name in general.items():
            cur.execute("""INSERT INTO lk_cause (code, description, cause_class) VALUES (%s, %s, %s)
                           ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description, cause_class = EXCLUDED.cause_class""",
                        (str(code), name, cause_class.get(str(code), 'Human')))
        for code, name in dom['IncidentTypeCategory'].items():
            cur.execute("""INSERT INTO lk_incident_type (code, description) VALUES (%s, %s)
                           ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description""", (code, name))
        for code, name in dom['PredominantFuelGroup'].items():
            cur.execute("""INSERT INTO lk_fuel_group (code, description) VALUES (%s, %s)
                           ON CONFLICT (code) DO NOTHING""", (code, name))
    print('  causes:', len(general), 'incident types:', len(dom['IncidentTypeCategory']), 'fuel groups:', len(dom['PredominantFuelGroup']))


if __name__ == '__main__':
    with connect() as conn:
        load_states_counties(conn)
        load_domains(conn)
        conn.commit()

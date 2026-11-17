"""USGS 3DEP LiDAR collections -> lidar_collection, and fire coverage -> event_lidar.

  python lidar.py index          # load every 3DEP Entwine (EPT) collection outline
  python lidar.py link [--min-acres 5000]   # find pre/post-fire LiDAR for each fire

The index lists each collection's EPT URL (streamable point cloud on AWS, no key) and boundary.
Acquisition year comes from the collection name (OR_Lane_County_2021 -> 2021; newer work units
end in a letter + 2-digit year: OR_WWildfires_1_A22 -> 2022); collections
in the same year as the fire are left unassigned because the name alone can't say before/after.
"""
import argparse
import json

from common import cached, connect, run_sql

INDEX = 'https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson'


def load_index(conn):
    p = cached(INDEX, 'ept_resources.geojson')
    feats = json.loads(p.read_text())['features']
    with conn.cursor() as cur:
        for f in feats:
            pr = f['properties']
            cur.execute("""
                INSERT INTO lidar_collection (sensor_id, name, acquired_from, acquired_to, ept_url, points_per_m2, boundary)
                SELECT 30, %(n)s, make_date(y, 1, 1), make_date(y, 12, 31), %(u)s,
                       %(c)s / nullif(ST_Area(g::geography), 0), g
                FROM (SELECT ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%(g)s), 4326)), 3)) AS g,
                             coalesce((SELECT max(m[1])::int FROM regexp_matches(%(n)s, '(?:^|_)((?:19|20)[0-9]{2})(?=_|$)', 'g') AS m),
                                      2000 + (regexp_match(%(n)s, '_[A-Z]([0-9]{2})$'))[1]::int) AS y) q
                ON CONFLICT (name) DO UPDATE SET ept_url = EXCLUDED.ept_url, boundary = EXCLUDED.boundary,
                    points_per_m2 = EXCLUDED.points_per_m2, acquired_from = EXCLUDED.acquired_from, acquired_to = EXCLUDED.acquired_to
            """, dict(n=pr['name'], u=pr['url'], c=pr.get('count') or 0, g=json.dumps(f['geometry'])))
    print('  lidar collections:', run_sql(conn, 'SELECT count(*), count(acquired_from) FROM lidar_collection')[0])


def link_events(conn, min_acres):
    rows = run_sql(conn, """
        WITH f AS (
            SELECT event_id, final_perimeter AS g, extract(year FROM discovered_at)::int AS fy, discovered_at, contained_at
            FROM fire_event
            WHERE final_perimeter IS NOT NULL AND coalesce(reported_acres, final_acres) >= %s AND discovered_at IS NOT NULL
        ), hits AS (
            SELECT f.event_id, c.collection_id, extract(year FROM c.acquired_from)::int AS cy, f.fy,
                   ST_Area(ST_Intersection(f.g, c.boundary)::geography) / nullif(ST_Area(f.g::geography), 0) AS cov,
                   f.discovered_at, f.contained_at, c.acquired_from, c.acquired_to
            FROM f JOIN lidar_collection c ON c.boundary && f.g AND ST_Intersects(c.boundary, f.g)
            WHERE c.acquired_from IS NOT NULL
        )
        INSERT INTO event_lidar (event_id, collection_id, phase, days_from_fire, coverage_frac)
        SELECT event_id, collection_id,
               CASE WHEN cy < fy THEN 'pre' ELSE 'post' END,
               CASE WHEN cy < fy THEN (acquired_to - discovered_at::date)
                    ELSE (acquired_from - coalesce(contained_at, discovered_at)::date) END,
               least(1, cov)
        FROM hits
        WHERE cy <> fy AND cov >= 0.05
        ON CONFLICT (event_id, collection_id) DO UPDATE SET phase = EXCLUDED.phase, coverage_frac = EXCLUDED.coverage_frac,
            days_from_fire = EXCLUDED.days_from_fire
        RETURNING phase
    """, (min_acres,))
    print('  event_lidar links:', len(rows or []))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=['index', 'link'])
    ap.add_argument('--min-acres', type=float, default=5000)
    a = ap.parse_args()
    with connect() as conn:
        if a.what == 'index':
            load_index(conn)
        else:
            link_events(conn, a.min_acres)
        conn.commit()

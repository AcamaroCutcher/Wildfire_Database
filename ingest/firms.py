"""NASA FIRMS active-fire detections -> hotspot.

  python firms.py files fire_archive_*.csv fire_nrt_*.csv      # FIRMS archive/NRT downloads (CSV)
  python firms.py api --source VIIRS_NOAA21_NRT --bbox -125,41,-116,47 --days 2   # needs FIRMS_MAP_KEY

Works for MODIS (C6.1) and VIIRS (C2) files. Note: Suomi-NPP products ended on 2026-11-02;
for live data use VIIRS_NOAA20_NRT and VIIRS_NOAA21_NRT.
"""
import argparse
import io
import os
import sys

from common import connect, curl, run_sql

SATELLITE = {  # FIRMS "satellite" column -> sensor.code
    'Terra': 'MODIS_TERRA', 'T': 'MODIS_TERRA', 'Aqua': 'MODIS_AQUA', 'A': 'MODIS_AQUA',
    'SNPP': 'VIIRS_SNPP', 'N': 'VIIRS_SNPP', 'N20': 'VIIRS_NOAA20', '1': 'VIIRS_NOAA20', 'N21': 'VIIRS_NOAA21', '2': 'VIIRS_NOAA21',
}
COLS = ['latitude', 'longitude', 'brightness', 'scan', 'track', 'acq_date', 'acq_time', 'satellite', 'instrument',
        'confidence', 'version', 'bright_t31', 'frp', 'daynight', 'type']


def stage(conn, fileobj, header):
    """COPY one FIRMS CSV into an unlogged staging table (all text)."""
    run_sql(conn, 'DROP TABLE IF EXISTS stg_firms; CREATE UNLOGGED TABLE stg_firms (%s)' % ', '.join(f'{c} text' for c in COLS))
    present = [c.strip().lower() for c in header.split(',')]
    present = ['bright_t31' if c == 'bright_ti5' else 'brightness' if c == 'bright_ti4' else c for c in present]
    unknown = [c for c in present if c not in COLS]
    if unknown:
        raise SystemExit(f'unexpected FIRMS columns: {unknown}')
    with conn.cursor() as cur, cur.copy(f'COPY stg_firms ({", ".join(present)}) FROM STDIN (FORMAT csv)') as cp:
        while True:
            block = fileobj.read(8 << 20)
            if not block:
                break
            cp.write(block)
    return run_sql(conn, 'SELECT count(*), min(acq_date), max(acq_date) FROM stg_firms')[0]


def merge(conn):
    sat_map = ', '.join(f"('{k}', '{v}')" for k, v in SATELLITE.items())
    lo, hi = run_sql(conn, 'SELECT min(acq_date)::date, max(acq_date)::date FROM stg_firms')[0]
    run_sql(conn, 'SELECT ensure_hotspot_partitions(%s, %s)', (lo, hi))
    sql = f"""
        WITH m(sat, code) AS (VALUES {sat_map}),
        src AS (
            SELECT s.*, se.sensor_id,
                   to_timestamp(s.acq_date || lpad(s.acq_time, 4, '0'), 'YYYY-MM-DDHH24MI') AS t,
                   CASE WHEN s.confidence ~ '^[0-9]+$' THEN s.confidence::smallint END AS pct
            FROM stg_firms s
            JOIN m ON m.sat = s.satellite
            JOIN sensor se ON se.code = m.code
        )
        INSERT INTO hotspot (sensor_id, acq_time, latitude, longitude, scan_km, track_km, brightness_k, brightness2_k, frp_mw,
                             confidence_raw, confidence_pct, confidence_class, daynight, fire_type, data_version, product)
        SELECT sensor_id, t, latitude::float8, longitude::float8, nullif(scan, '')::real, nullif(track, '')::real,
               nullif(brightness, '')::real, nullif(bright_t31, '')::real, nullif(frp, '')::real,
               confidence, pct, confidence_class_of(confidence, pct), nullif(daynight, ''), nullif(type, '')::smallint,
               CASE WHEN version ILIKE '%URT%' THEN 'URT' WHEN version ILIKE '%RT%' AND version NOT ILIKE '%NRT%' THEN 'RT'
                    WHEN version ILIKE '%NRT%' THEN 'NRT' ELSE 'SP' END,
               instrument || ' ' || version
        FROM src
        ON CONFLICT (sensor_id, acq_time, latitude, longitude) DO NOTHING
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        n = cur.rowcount
    # where a standard (SP) record now exists, drop near-real-time duplicates of the same pass
    run_sql(conn, """
        DELETE FROM hotspot n USING hotspot s
        WHERE n.data_version <> 'SP' AND s.data_version = 'SP'
          AND n.sensor_id = s.sensor_id AND n.acq_time = s.acq_time
          AND n.acq_time BETWEEN %s AND %s::date + 1
          AND ST_DWithin(n.geom, s.geom, 0.002)
    """, (lo, hi))
    run_sql(conn, 'DROP TABLE stg_firms')
    return n, lo, hi


def load_files(paths):
    for p in paths:
        with connect() as conn, open(p, newline='', encoding='utf-8-sig') as f:
            header = f.readline().strip()
            cnt, lo, hi = stage(conn, f, header)
            n, _, _ = merge(conn)
            conn.commit()
            print(f'  {os.path.basename(p)}: {cnt:,} rows ({lo} … {hi}), {n:,} new')


def load_api(source, bbox, days, date=None):
    key = os.environ.get('FIRMS_MAP_KEY')
    if not key:
        raise SystemExit('set FIRMS_MAP_KEY (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/)')
    url = f'https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}' + (f'/{date}' if date else '')
    text = curl(url).decode()
    if not text.startswith('latitude'):
        raise SystemExit(f'FIRMS answered: {text[:200]}')
    header, body = text.split('\n', 1)
    with connect() as conn:
        cnt, lo, hi = stage(conn, io.StringIO(body), header)
        n, _, _ = merge(conn)
        conn.commit()
    print(f'  {source}: {cnt:,} detections ({lo} … {hi}), {n:,} new')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('files'); f.add_argument('paths', nargs='+')
    a = sub.add_parser('api'); a.add_argument('--source', default='VIIRS_NOAA21_NRT'); a.add_argument('--bbox', default='-125,41,-116,47')
    a.add_argument('--days', type=int, default=2); a.add_argument('--date')
    args = ap.parse_args()
    if args.cmd == 'files':
        load_files(args.paths)
    else:
        load_api(args.source, args.bbox, args.days, args.date)
    sys.exit(0)

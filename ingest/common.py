"""Shared helpers for the ingest scripts.

Database: set WF_DSN (default "dbname=wildfire host=localhost port=5432").
Tools: GDAL's ogr2ogr (bundled with Postgres.app) loads vector data straight from ArcGIS services,
and curl is used for downloads because some system Pythons ship an old LibreSSL that fails on modern TLS.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get('WF_CACHE', ROOT / 'data' / 'raw'))
CACHE.mkdir(parents=True, exist_ok=True)
DSN = os.environ.get('WF_DSN', 'dbname=wildfire host=localhost port=5432')

PG_BIN = os.environ.get('PG_BIN', '/Applications/Postgres.app/Contents/Versions/17/bin')
OGR2OGR = shutil.which('ogr2ogr') or str(Path(PG_BIN) / 'ogr2ogr')

NIFC = 'https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services'


def connect(autocommit=False):
    # every session in UTC, so text timestamps are read as UTC and nothing shifts
    return psycopg.connect(DSN, autocommit=autocommit, options='-c TimeZone=UTC')


def curl(url, dest=None, retries=4, timeout=600):
    """Download url (to dest, or return bytes). Retries with exponential backoff."""
    for attempt in range(retries):
        cmd = ['curl', '-sSfL', '--http1.1', '--max-time', str(timeout), url]
        if dest:
            cmd[1:1] = ['-o', str(dest)]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0:
            return dest if dest else r.stdout
        time.sleep(2 ** attempt)
    raise RuntimeError(f'download failed: {url}: {r.stderr.decode()[:300]}')


def get_json(url):
    return json.loads(curl(url))


def cached(url, name):
    """Download once into data/raw/name."""
    p = CACHE / name
    if not p.exists() or p.stat().st_size == 0:
        print(f'  downloading {name} …')
        curl(url, p)
    return p


def ogr_to_pg(src, table, extra=(), where=None):
    """Load any OGR-readable source (file, zip, ArcGIS query URL) into a staging table."""
    pg = 'PG:' + DSN
    cmd = [OGR2OGR, '-f', 'PostgreSQL', pg, src, '-nln', table, '-overwrite',
           '-lco', 'GEOMETRY_NAME=geom', '-lco', 'FID=ogc_fid', '-lco', 'SPATIAL_INDEX=NONE',
           '-t_srs', 'EPSG:4326', '-nlt', 'PROMOTE_TO_MULTI', '-makevalid', '--config', 'PG_USE_COPY', 'YES', *extra]
    if where:
        cmd += ['-where', where]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'ogr2ogr failed for {table}: {r.stderr[-800:]}')
    return table


def arcgis_query_url(service, layer=0, where='1=1', fields='*'):
    from urllib.parse import quote
    return (f'ESRIJSON:{NIFC}/{service}/FeatureServer/{layer}/query?where={quote(where)}'
            f'&outFields={fields}&outSR=4326&f=json')


def run_sql(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg.ProgrammingError:
            return None


def arcgis_fetch_geojson(service, name, where='1=1', fields='*', layer=0, page=500, refresh=False):
    """Page through an ArcGIS FeatureServer layer into one cached GeoJSON file (dates stay epoch ms)."""
    from urllib.parse import quote
    out = CACHE / f'{name}.geojson'
    if out.exists() and not refresh:
        return out
    base = f'{NIFC}/{service}/FeatureServer/{layer}/query?where={quote(where)}&outFields={fields}&outSR=4326&f=geojson'
    feats, offset = [], 0
    while True:
        d = json.loads(curl(f'{base}&resultOffset={offset}&resultRecordCount={page}&orderByFields=OBJECTID'))
        fs = d.get('features', [])
        feats += fs
        offset += len(fs)
        if not fs or not (d.get('exceededTransferLimit') or d.get('properties', {}).get('exceededTransferLimit')) and len(fs) < page:
            break
        if offset % 5000 < page:
            print(f'    {name}: {offset:,} features')
    tmp = out.with_suffix('.tmp')
    tmp.write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}))
    tmp.rename(out)
    print(f'    {name}: {len(feats):,} features cached')
    return out

"""Numbers and query results for the guide page (data/stats.json, data/queries.json).

  python export/site_data.py --out ../ace.github.io/projects/wildfire-data/data
"""
import argparse
import datetime
import decimal
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'ingest'))
from common import connect  # noqa: E402


def cell(v):
    if v is None:
        return ''
    if isinstance(v, (datetime.datetime,)):
        return v.strftime('%Y-%m-%d %H:%M')
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, (float, decimal.Decimal)):
        f = float(v)
        return f'{f:,.2f}' if abs(f) < 1000 and f != int(f) else f'{f:,.0f}'
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(HERE.parents[1] / 'ace.github.io' / 'projects' / 'wildfire-data' / 'data'))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("SET statement_timeout = '15min'")
        s = {}
        s['hotspots'] = c.execute('SELECT count(*) FROM hotspot').fetchone()[0]
        s['events'] = c.execute('SELECT count(*) FROM fire_event').fetchone()[0]
        s['perimeters'] = c.execute('SELECT count(*) FROM perimeter_obs WHERE source = %s', ('WFIGS',)).fetchone()[0]
        s['lidar'] = c.execute('SELECT count(*) FROM lidar_collection').fetchone()[0]
        s['links'] = [list(r) for r in c.execute('SELECT method, count(*) FROM hotspot_event_link GROUP BY 1 ORDER BY 2 DESC').fetchall()]
        s['fires_with_pre_and_post_lidar'] = c.execute("""SELECT count(*) FROM (SELECT event_id FROM event_lidar WHERE coverage_frac >= 0.5
                                                         GROUP BY 1 HAVING bool_or(phase = 'pre') AND bool_or(phase = 'post')) q""").fetchone()[0]
        s['generated'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes')
        idx = HERE.parents[1] / 'ace.github.io' / 'projects' / 'wildfire-data' / 'app' / 'fires' / 'index.json'
        if idx.exists():
            cases = []
            for f in json.loads(idx.read_text()):
                m = json.loads((idx.parent / f['slug'] / 'manifest.json').read_text())
                b = json.loads((idx.parent / f['slug'] / 'bundle.json').read_text())
                lid = ' + '.join(f'{"before" if p == "pre" else "after"} {v["year"]}' for p, v in m['lidar'].items() if p != 'diff')
                cases.append(dict(slug=f['slug'], name=m['name'].title(), where=m['where'], when=m['discovered'][:7], acres=m['acres'],
                                  hotspots=len(b['hotspots']), perimeters=sum(1 for p in b['perimeters'] if p['properties']['source'] != 'VIIRS_DERIVED'),
                                  lidar='LiDAR ' + (lid or 'none'), why=m['why']))
            s['cases'] = cases
        (out / 'stats.json').write_text(json.dumps(s, indent=1, default=str))
        print('stats:', {k: v for k, v in s.items() if k != 'cases'})

        res = []
        for f in sorted((HERE.parent / 'queries' / 'proposal').glob('*.sql'), key=lambda p: (p.stem[0] != 'b', p.stem)):   # basic first
            text = f.read_text()
            lines = text.splitlines()
            question = lines[1].lstrip('- ').strip()
            sql = '\n'.join(lines[2:]).strip()
            t = time.perf_counter()
            cur = c.execute(sql)
            rows = cur.fetchall()
            ms = round((time.perf_counter() - t) * 1000)
            res.append(dict(id=f.stem, question=question, sql=sql, columns=[d.name for d in cur.description],
                            rows=[[cell(v) for v in r] for r in rows[:8]], rows_total=len(rows), ms=ms))
            print(f'  {f.stem}: {len(rows):,} rows in {ms:,} ms')
        (out / 'queries.json').write_text(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()

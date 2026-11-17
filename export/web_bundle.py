"""Build the web app's static bundle for each case fire.

  python export/web_bundle.py [slug ...] --out ../ace.github.io/projects/wildfire-data/app/fires

For each fire it writes into <out>/<slug>/:
  bundle.json         event, perimeters (mapped + VIIRS-derived progression), linked hotspots (from event_bundle())
  lidar_<phase>.bin   thinned LiDAR points: uint16 x, y (scaled to the box), uint16 z (dm above zmin), uint8 class
  chm_<phase>.png     canopy height (10 m cells), colour-mapped; chm_diff.png when both phases exist
  s2_pre.jpg, s2_post.jpg, swir_pre.jpg, swir_post.jpg, dnbr.png   Sentinel-2 before/after and burn severity
  manifest.json       bounds, dates, scene ids, LiDAR sources, statistics
and <out>/index.json listing all fires.
"""
import argparse
import json
import math
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'ingest'))
sys.path.insert(0, str(HERE))
from common import connect  # noqa: E402
import lidar_sample  # noqa: E402

CASES = {
    'beachie-creek-2020': dict(event_id=45160, where='Santiam Canyon, Oregon',
                               why='The 2020 Labor Day east-wind run: satellite detections trace it from about 1,100 acres on the afternoon of 7 September to about 115,000 acres by 2 a.m. the next morning. Post-fire LiDAR covers the whole burn.',
                               lidar={'post': 'OR_WWildfires_1_A22'}),
    'rail-ridge-2024': dict(event_id=487049, where='Grant County, Oregon',
                            why='Mapped almost daily (96 perimeters) and covered by LiDAR flown five years before the fire: spread over known canopy.',
                            lidar={'pre': 'OR_NRCSUSGS_2_2019'}),
    'red-apple-2021': dict(event_id=159988, where='Chelan County, Washington',
                           why='LiDAR from both 2019 and 2022, so the canopy that burned can be measured directly.',
                           lidar={'pre': 'WA_EasternCascades_6_2019', 'post': 'WA_CentralWildfire_1_D22'}),
}
TO3857 = Transformer.from_crs(4326, 3857, always_xy=True)
TO4326 = Transformer.from_crs(3857, 4326, always_xy=True)
EARTH_SEARCH = 'https://earth-search.aws.element84.com/v1'


def box_3857(bbox, margin_m=1500):
    x0, y0 = TO3857.transform(bbox[0], bbox[1])
    x1, y1 = TO3857.transform(bbox[2], bbox[3])
    return x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m


def lonlat_bounds(b):
    w, s = TO4326.transform(b[0], b[1])
    e, n = TO4326.transform(b[2], b[3])
    return [w, s, e, n]


# ---------------------------------------------------------------------------- fire record
def fire_record(conn, eid, out):
    conn.execute('SELECT derive_viirs_perimeters(%s)', (eid,))
    conn.execute('SELECT estimate_ignition(%s)', (eid,))
    conn.commit()
    b = conn.execute('SELECT event_bundle(%s, 80000)', (eid,)).fetchone()[0]
    (out / 'bundle.json').write_text(json.dumps(b, separators=(',', ':'), default=str))
    w, s, e, n = conn.execute('SELECT ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g) FROM (SELECT ST_Envelope(final_perimeter) g FROM fire_event WHERE event_id = %s) q', (eid,)).fetchone()
    return b, (w, s, e, n)


# ---------------------------------------------------------------------------- LiDAR
def lidar_read(conn, name, box, max_points=14_000_000, max_depth=12):
    ept = conn.execute('SELECT ept_url, acquired_from FROM lidar_collection WHERE name = %s', (name,)).fetchone()
    lon0, lat0 = TO4326.transform(box[0], box[1])
    lon1, lat1 = TO4326.transform(box[2], box[3])
    cache = HERE.parent / 'data' / 'cache' / f'lidar_{name}_{abs(hash(tuple(round(v) for v in box))) % 10**8}.npz'
    if cache.exists():                                   # LiDAR downloads are slow: keep them between runs
        z = np.load(cache, allow_pickle=True)
        p = {k: z[k] for k in z.files if k != 'meta'}
        p['meta'] = z['meta'].item()
    else:
        p = lidar_sample.read_box(ept[0], lon0, lat0, lon1, lat1, max_points=max_points, max_depth=max_depth)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, **{k: v for k, v in p.items() if k != 'meta'}, meta=np.array(p['meta'], dtype=object))
    p['year'] = ept[1].year if ept[1] else None
    return p


def density(p, box, cell=20.0):
    """Points per covered 20 m cell (cells with no points are outside the collection)."""
    nx = int(math.ceil((box[2] - box[0]) / cell))
    flat = ((box[3] - p['y']) // cell).astype(int) * nx + ((p['x'] - box[0]) // cell).astype(int)
    covered = len(np.unique(flat))
    return len(flat) / max(covered, 1)


def thin_to(p, frac, seed=11):
    """Random subsample so two epochs have the same density before comparing canopy heights."""
    if frac >= 1:
        return p
    m = np.random.default_rng(seed).random(len(p['x'])) < frac
    return {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in p.items()}


def lidar_products(p, box, cell=30.0, web_points=600_000):
    x, y, z, cls = p['x'], p['y'], p['z'], p['cls']
    # canopy height model on a cell grid (3857 metres; good enough over a fire, ~1/cos(lat) stretch noted in manifest)
    nx, ny = int(math.ceil((box[2] - box[0]) / cell)), int(math.ceil((box[3] - box[1]) / cell))
    ix = np.clip(((x - box[0]) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((box[3] - y) / cell).astype(int), 0, ny - 1)
    flat = iy * nx + ix
    ground = np.full(nx * ny, np.inf)
    np.minimum.at(ground, flat[cls == 2], z[cls == 2])
    top = np.full(nx * ny, -np.inf)
    np.maximum.at(top, flat, z)
    g = ground.reshape(ny, nx)
    have = np.isfinite(g)
    if have.any():                                   # fill cells without ground returns from the nearest ground cell
        idx = ndimage.distance_transform_edt(~have, return_distances=False, return_indices=True)
        g = g[tuple(idx)]
    top = top.reshape(ny, nx)
    chm = np.full((ny, nx), np.nan, np.float32)
    ok = np.isfinite(top)                            # cells with no LiDAR points are 'no data', never 0 m
    chm[ok] = np.clip(top[ok] - g[ok], 0, 90)
    # thinned points for the browser: every ground-adjacent and canopy point has the same chance
    keep = np.random.default_rng(7).permutation(len(x))[:web_points]
    xs, ys, zs, cs = x[keep], y[keep], z[keep], cls[keep]
    zmin = float(np.percentile(z, 0.05))
    sx = (box[2] - box[0]) / 65535.0
    sy = (box[3] - box[1]) / 65535.0
    rec = np.zeros(len(xs), dtype=[('x', '<u2'), ('y', '<u2'), ('z', '<u2'), ('c', 'u1'), ('h', 'u1')])
    rec['x'] = np.clip((xs - box[0]) / sx, 0, 65535)
    rec['y'] = np.clip((ys - box[1]) / sy, 0, 65535)
    rec['z'] = np.clip((zs - zmin) * 10, 0, 65535)
    rec['c'] = np.where(cs == 2, 2, np.where(cs == 9, 9, 1))
    hg = (zs - g[np.clip(((box[3] - ys) / cell).astype(int), 0, ny - 1), np.clip(((xs - box[0]) / cell).astype(int), 0, nx - 1)])
    rec['h'] = np.clip(hg, 0, 255)                  # height above ground, metres (for colouring)
    return dict(points=rec, chm=chm, zmin=zmin, sx=sx, sy=sy, meta=p['meta'], year=p['year'], grid=(nx, ny, cell))


CHM_RAMP = [(0, (40, 34, 24, 0)), (1, (120, 110, 70, 160)), (5, (150, 170, 80, 220)), (15, (80, 150, 60, 235)), (30, (30, 110, 50, 245)), (50, (10, 70, 40, 255))]
DIFF_RAMP = [(-40, (190, 30, 20, 255)), (-15, (240, 110, 40, 240)), (-3, (250, 200, 120, 160)), (0, (0, 0, 0, 0)), (3, (150, 210, 150, 160)), (15, (40, 150, 80, 230))]
DNBR_CLASSES = [(-0.25, (26, 120, 50, 200)), (-0.1, (120, 180, 100, 140)), (0.1, (0, 0, 0, 0)), (0.27, (250, 230, 120, 200)),
                (0.44, (245, 160, 60, 220)), (0.66, (225, 70, 30, 235)), (9, (150, 20, 40, 245))]


def ramp_png(arr, ramp, path):
    out = np.zeros(arr.shape + (4,), np.uint8)
    v = np.nan_to_num(arr, nan=-999)
    for i in range(4):
        xs = [r[0] for r in ramp]; ys = [r[1][i] for r in ramp]
        out[..., i] = np.interp(v, xs, ys).astype(np.uint8)
    out[np.isnan(arr)] = 0
    Image.fromarray(out, 'RGBA').quantize(colors=48, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)


def classes_png(arr, classes, path):
    out = np.zeros(arr.shape + (4,), np.uint8)
    lo = -9
    for hi, col in classes:
        m = (arr >= lo) & (arr < hi)
        out[m] = col
        lo = hi
    out[np.isnan(arr)] = 0
    Image.fromarray(out, 'RGBA').save(path, optimize=True)


def detail(conn, c, box, chms, out, meta, size_m=2500.0, cell=30.0):
    """A dense LiDAR tile (full detail) centred on the tallest canopy inside the burn, for the close-up."""
    from rasterio.features import geometry_mask
    from shapely import wkb
    ref = chms.get('pre', next(iter(chms.values())))
    perim = conn.execute('SELECT ST_AsBinary(ST_Transform(final_perimeter, 3857)) FROM fire_event WHERE event_id = %s', (c['event_id'],)).fetchone()[0]
    ny, nx = ref.shape
    inside = ~geometry_mask([wkb.loads(bytes(perim))], (ny, nx), from_origin(box[0], box[3], cell, cell))
    k = int(size_m / cell)
    smooth = ndimage.uniform_filter(np.nan_to_num(ref), k) * ndimage.uniform_filter(inside.astype(float), k)
    smooth[~inside] = 0
    j, i = np.unravel_index(np.argmax(smooth), smooth.shape)
    cx, cy = box[0] + (i + 0.5) * cell, box[3] - (j + 0.5) * cell
    dbox = (cx - size_m / 2, cy - size_m / 2, cx + size_m / 2, cy + size_m / 2)
    meta['detail'] = dict(bounds=lonlat_bounds(dbox), bounds_3857=list(dbox), size_m=size_m)
    for phase, name in c['lidar'].items():
        p = lidar_read(conn, name, dbox, max_points=4_000_000, max_depth=14)
        L = lidar_products(p, dbox, cell=5.0, web_points=1_000_000)
        L['points'].tofile(out / f'lidar_detail_{phase}.bin')
        meta['detail'][phase] = dict(zmin=L['zmin'], sx=L['sx'], sy=L['sy'], points=int(len(L['points'])), raw=L['meta'])
        print(f'[{c["event_id"]}] detail {phase}: {len(L["points"]):,} points, depth {L["meta"]["depth"]}')


# ---------------------------------------------------------------------------- Sentinel-2
def s2_search(bbox, start, end):
    from pystac_client import Client
    c = Client.open(EARTH_SEARCH)
    items = list(c.search(collections=['sentinel-2-l2a'], bbox=bbox, datetime=f'{start}/{end}',
                          query={'eo:cloud_cover': {'lt': 15}, 's2:snow_ice_percentage': {'lt': 2}}, max_items=200).items())
    return items


def pick_scenes(items, bbox):
    """Pick the clearest date whose tiles cover the box, and return all its tiles."""
    from collections import defaultdict
    by_day = defaultdict(list)
    for it in items:
        by_day[it.datetime.date()].append(it)
    def cover(its):
        from shapely.geometry import box as sbox, shape
        from shapely.ops import unary_union
        b = sbox(*bbox)
        return unary_union([shape(i.geometry) for i in its]).intersection(b).area / b.area
    scored = sorted(by_day.items(), key=lambda kv: (-round(cover(kv[1]), 2), np.mean([i.properties['eo:cloud_cover'] for i in kv[1]])))
    for day, its in scored:
        if cover(its) > 0.95:
            return day, its
    return scored[0] if scored else (None, [])


def s2_bands(items, box, res=20.0):
    """Mosaic B02, B03, B04, B8A, B12 of the given items onto a 3857 grid. Returns reflectance 0-1."""
    nx, ny = int((box[2] - box[0]) / res), int((box[3] - box[1]) / res)
    dst_t = from_origin(box[0], box[3], res, res)
    bands = {'blue': 'blue', 'green': 'green', 'red': 'red', 'nir08': 'nir08', 'swir22': 'swir22'}
    out = {k: np.full((ny, nx), np.nan, np.float32) for k in bands}
    for it in items:
        offset = 0.0
        if not it.properties.get('earthsearch:boa_offset_applied', True) and float(it.properties.get('s2:processing_baseline', '0')) >= 4.0:
            offset = -1000.0
        for k, asset in bands.items():
            href = it.assets[asset].href
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', AWS_NO_SIGN_REQUEST='YES'):
                with rasterio.open(href) as src:
                    tmp = np.full((ny, nx), np.nan, np.float32)
                    reproject(rasterio.band(src, 1), tmp, dst_transform=dst_t, dst_crs='EPSG:3857',
                              resampling=Resampling.bilinear, src_nodata=0, dst_nodata=np.nan)
            v = (tmp + offset) / 10000.0
            fill = np.isnan(out[k]) & ~np.isnan(v)
            out[k][fill] = v[fill]
    return out


def to_jpg(r, g, b, path, gain=3.2):
    rgb = np.dstack([r, g, b]) * gain
    rgb = np.clip(np.nan_to_num(rgb) ** (1 / 1.3) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(rgb).save(path, quality=82, optimize=True)


def imagery(meta, bbox_ll, box, out, discovered, contained):
    pre_items = s2_search(bbox_ll, (discovered - timedelta(days=75)).isoformat(), (discovered - timedelta(days=2)).isoformat())
    end = (contained or discovered + timedelta(days=45))
    if end.month >= 9 or end.month <= 3:
        # late-season fire: compare with the next growing season (MTBS "extended assessment"), not a snowy winter scene
        y = end.year + (1 if end.month >= 9 else 0)
        post_from, post_to = f'{y}-06-15', f'{y}-09-15'
    else:
        post_from, post_to = (end + timedelta(days=3)).isoformat(), (end + timedelta(days=110)).isoformat()
    post_items = s2_search(bbox_ll, post_from, post_to)
    meta['imagery_rule'] = 'post scene: next growing season (extended assessment)' if post_from.endswith('06-15') else 'post scene: soon after containment'
    res = {}
    for phase, items in (('pre', pre_items), ('post', post_items)):
        day, its = pick_scenes(items, bbox_ll)
        if not its:
            continue
        b = s2_bands(its, box)
        to_jpg(b['red'], b['green'], b['blue'], out / f's2_{phase}.jpg')
        to_jpg(b['swir22'], b['nir08'], b['red'], out / f'swir_{phase}.jpg', gain=2.4)
        res[phase] = dict(date=str(day), items=[i.id for i in its], cloud=[i.properties['eo:cloud_cover'] for i in its], bands=b)
    if 'pre' in res and 'post' in res:
        nbr = lambda b: (b['nir08'] - b['swir22']) / (b['nir08'] + b['swir22'] + 1e-6)
        ndwi = lambda b: (b['green'] - b['nir08']) / (b['green'] + b['nir08'] + 1e-6)
        dnbr = nbr(res['pre']['bands']) - nbr(res['post']['bands'])
        water = (ndwi(res['pre']['bands']) > 0) | (ndwi(res['post']['bands']) > 0)   # rivers, lakes: not burn severity
        dnbr[water] = np.nan
        classes_png(dnbr, DNBR_CLASSES, out / 'dnbr.png')
        meta['dnbr'] = dict(mean=float(np.nanmean(dnbr)), high_severity_frac=float(np.nanmean(dnbr > 0.66)))
    for v in res.values():
        v.pop('bands')
    meta['imagery'] = res


# ---------------------------------------------------------------------------- main
def build(conn, slug, root):
    c = CASES[slug]
    out = root / slug
    out.mkdir(parents=True, exist_ok=True)
    b, bbox = fire_record(conn, c['event_id'], out)
    box = box_3857(bbox)
    disc, cont = conn.execute('SELECT discovered_at::date, coalesce(contained_at, out_at)::date FROM fire_event WHERE event_id = %s', (c['event_id'],)).fetchone()
    meta = dict(slug=slug, event_id=c['event_id'], name=b['event']['incident_name'], where=c['where'], why=c['why'],
                discovered=str(disc), contained=str(cont) if cont else None, acres=b['event']['acres_burned'],
                bounds=lonlat_bounds(box), bounds_3857=list(box), lidar={}, files={})
    print(f'[{slug}] hotspots {len(b["hotspots"]):,}, perimeters {len(b["perimeters"])}')
    chms = {}
    raw = {phase: lidar_read(conn, name, box) for phase, name in c['lidar'].items()}
    if len(raw) == 2:                                    # compare like with like: equal point density
        d = {ph: density(p, box) for ph, p in raw.items()}
        lo = min(d.values())
        raw = {ph: thin_to(p, lo / d[ph]) for ph, p in raw.items()}
        meta['lidar_density_matched'] = {ph: round(v, 1) for ph, v in d.items()}
    for phase, name in c['lidar'].items():
        L = lidar_products(raw[phase], box)
        L['points'].tofile(out / f'lidar_{phase}.bin')
        ramp_png(L['chm'], CHM_RAMP, out / f'chm_{phase}.png')
        chms[phase] = L['chm']
        meta['lidar'][phase] = dict(collection=name, year=L['year'], zmin=L['zmin'], sx=L['sx'], sy=L['sy'], points=int(len(L['points'])),
                                    raw=L['meta'], cell_m=L['grid'][2], chm_mean=float(np.nanmean(L['chm'])), chm_p95=float(np.nanpercentile(L['chm'], 95)))
        print(f'[{slug}] lidar {phase}: {len(L["points"]):,} web points of {L["meta"]["points"]:,}, depth {L["meta"]["depth"]}')
    if chms:
        detail(conn, c, box, chms, out, meta)
    if 'pre' in chms and 'post' in chms:
        d = chms['post'] - chms['pre']                  # NaN wherever either epoch has no data
        ramp_png(d, DIFF_RAMP, out / 'chm_diff.png')
        with conn.cursor() as cur:                      # change inside the burn vs outside it
            cur.execute('SELECT ST_AsBinary(ST_Transform(final_perimeter, 3857)) FROM fire_event WHERE event_id = %s', (c['event_id'],))
            from shapely import wkb
            from rasterio.features import geometry_mask
            nx, ny = d.shape[1], d.shape[0]
            inside = ~geometry_mask([wkb.loads(bytes(cur.fetchone()[0]))], (ny, nx), from_origin(box[0], box[3], 30.0, 30.0))
        both = ~np.isnan(d)
        meta['lidar']['diff'] = dict(
            overlap_frac=float(both.mean()),
            mean_change_inside_m=float(np.nanmean(d[inside & both])) if (inside & both).any() else None,
            mean_change_outside_m=float(np.nanmean(d[~inside & both])) if (~inside & both).any() else None,
            lost_over_5m_inside=float(np.nanmean(d[inside & both] < -5)) if (inside & both).any() else None)
    try:
        imagery(meta, bbox, box, out, disc, cont)
    except Exception as e:                                # imagery is optional; never block the bundle
        meta['imagery_error'] = str(e)[:300]
        print(f'[{slug}] imagery failed: {e}')
    meta['files'] = sorted(p.name for p in out.iterdir() if p.name != 'manifest.json')
    (out / 'manifest.json').write_text(json.dumps(meta, indent=1, default=str))
    return meta


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('slugs', nargs='*', default=list(CASES))
    ap.add_argument('--out', default=str(HERE.parents[1] / 'ace.github.io' / 'projects' / 'wildfire-data' / 'app' / 'fires'))
    a = ap.parse_args()
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        metas = [build(conn, s, root) for s in a.slugs]
    idx_path = root / 'index.json'
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else []
    idx = [m for m in idx if m['slug'] not in a.slugs] + [{k: m[k] for k in ('slug', 'name', 'where', 'why', 'discovered', 'contained', 'acres', 'bounds')} | {'lidar': {p: v['collection'] for p, v in m['lidar'].items() if p != 'diff'}} for m in metas]
    idx_path.write_text(json.dumps(idx, indent=1, default=str))

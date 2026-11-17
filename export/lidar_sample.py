"""Read a thinned LiDAR sample over a box from a USGS 3DEP Entwine Point Tile (EPT) collection.

EPT is a level-of-detail octree: shallow levels hold a sparse, even sample of the whole cloud and
each deeper level adds detail. Reading every node down to a chosen depth inside a box gives a
uniformly thinned point cloud without downloading the billions of points underneath.

    pts = read_box(ept_url, west, south, east, north, max_points=6_000_000)
Returns a dict of numpy arrays in EPSG:3857 (x, y) and metres (z), plus classification.
"""
import io
import json
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor

import laspy
import numpy as np
from pyproj import Transformer

TO3857 = Transformer.from_crs(4326, 3857, always_xy=True)


def _get(url, retries=4):
    for i in range(retries):
        r = subprocess.run(['curl', '-sSfL', '--http1.1', '--max-time', '120', url], capture_output=True)
        if r.returncode == 0:
            return r.stdout
    raise RuntimeError(f'failed: {url}')


def _node_bounds(root, key):
    d, x, y, z = map(int, key.split('-'))
    w = (root[3] - root[0]) / 2 ** d
    return (root[0] + x * w, root[1] + y * w, root[2] + z * w, root[0] + (x + 1) * w, root[1] + (y + 1) * w, root[2] + (z + 1) * w)


def _hierarchy(base, root, box, max_depth):
    """Yield (key, count) for nodes intersecting box down to max_depth, following sub-hierarchy files."""
    def walk(key):
        h = json.loads(_get(f'{base}/ept-hierarchy/{key}.json'))
        out = []
        for k, c in h.items():
            d = int(k.split('-')[0])
            if d > max_depth:
                continue
            b = _node_bounds(root, k)
            if b[3] < box[0] or b[0] > box[2] or b[4] < box[1] or b[1] > box[3]:
                continue
            if c == -1:                              # deeper hierarchy lives in its own file
                if d < max_depth:
                    out += walk(k)
                continue
            out.append((k, c))
        return out
    return walk('0-0-0-0')


def read_box(ept_url, west, south, east, north, max_points=6_000_000, max_depth=12, threads=16):
    base = ept_url.rsplit('/', 1)[0]
    meta = json.loads(_get(ept_url))
    root = meta['bounds']
    x0, y0 = TO3857.transform(west, south)
    x1, y1 = TO3857.transform(east, north)
    box = (x0, y0, x1, y1)
    nodes = _hierarchy(base, root, box, max_depth)
    # deepest depth whose cumulative count stays under the budget (counts are for whole nodes; ~ within box)
    by_depth = {}
    for k, c in nodes:
        by_depth.setdefault(int(k.split('-')[0]), []).append((k, c))
    keep, total = [], 0
    for d in sorted(by_depth):
        layer = sum(c for _, c in by_depth[d])
        if total + layer > max_points * 1.6 and keep:
            break
        keep += [k for k, _ in by_depth[d]]
        total += layer
    depth_used = max(int(k.split('-')[0]) for k in keep)

    def fetch(key):
        las = laspy.read(io.BytesIO(_get(f'{base}/ept-data/{key}.laz')))
        cls = np.asarray(las.classification)
        m = (las.x >= x0) & (las.x <= x1) & (las.y >= y0) & (las.y <= y1)
        m &= ~np.isin(cls, (7, 18))                         # low / high noise
        try:
            m &= ~np.asarray(las.withheld, dtype=bool)      # flagged for exclusion by the producer
        except Exception:
            pass
        return (np.asarray(las.x[m]), np.asarray(las.y[m]), np.asarray(las.z[m]),
                np.asarray(las.classification[m]), np.asarray(las.return_number[m]), np.asarray(las.number_of_returns[m]))

    with ThreadPoolExecutor(threads) as ex:
        parts = list(ex.map(fetch, keep))
    cat = lambda i: np.concatenate([p[i] for p in parts]) if parts else np.array([])
    pts = dict(x=cat(0), y=cat(1), z=cat(2), cls=cat(3).astype(np.uint8), ret=cat(4).astype(np.uint8), nret=cat(5).astype(np.uint8))
    pts['meta'] = dict(nodes=len(keep), depth=depth_used, points=int(len(pts['x'])), ept=ept_url)
    return pts

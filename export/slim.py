"""Shrink a case-fire bundle for the web: simplify perimeters (~10 m), round coordinates, cap image size."""
import json
import sys
from pathlib import Path

from PIL import Image
from shapely.geometry import mapping, shape


def rnd(c, n=4):
    return [rnd(x, n) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], n), round(c[1], n)]


def drop_specks(g, min_deg2=2e-6):
    """Remove islands and holes smaller than ~2 ha (min_deg2 in degrees^2 at mid-latitudes): far below one 375 m pixel."""
    from shapely.geometry import MultiPolygon, Polygon
    polys = list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]
    keep = [Polygon(p.exterior, [r for r in p.interiors if Polygon(r).area >= min_deg2]) for p in polys if p.area >= min_deg2]
    if not keep:
        keep = [max(polys, key=lambda p: p.area)]
    return MultiPolygon(keep) if len(keep) > 1 else keep[0]


def slim(d: Path, max_px=1800):
    b = json.loads((d / 'bundle.json').read_text())
    for f in b['perimeters']:
        tol = 0.0003   # ~25 m: far finer than the 375 m satellite pixels and the map scale
        g = drop_specks(shape(f['geometry'])).simplify(tol, preserve_topology=True)
        f['geometry'] = {'type': g.geom_type, 'coordinates': rnd(mapping(g)['coordinates'])}
    (d / 'bundle.json').write_text(json.dumps(b, separators=(',', ':'), default=str))
    for p in list(d.glob('*.jpg')):
        im = Image.open(p)
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        im.save(p, quality=74, optimize=True, progressive=True)
    for p in d.glob('*.png'):
        im = Image.open(p)
        if max(im.size) > max_px:
            im = im.convert('RGBA'); im.thumbnail((max_px, max_px), Image.NEAREST)
            im.quantize(colors=48, method=Image.Quantize.FASTOCTREE).save(p, optimize=True)


if __name__ == '__main__':
    for a in sys.argv[1:]:
        slim(Path(a))

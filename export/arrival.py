"""Fire arrival-time map: when did the fire reach each cell of the burn?

An interpolation of observations, not a physics model. Steps:

  1. Snapshots. Group detections into satellite passes (gaps < 20 min). At each pass, the area known
     to have burned is every detection so far drawn as a disc the size of its own pixel (VIIRS ~375 m
     at nadir, up to ~800 m at the swath edge; MODIS 1-2 km), smoothed into one shape with enclosed
     holes filled; every mapped perimeter is a snapshot too. Snapshots only grow.
  2. Gaps. Cells inside the final perimeter that no snapshot covers take the snapshot of the nearest
     covered cell.
  3. Timing between snapshots. Ground that first appears at snapshot k burned between snapshot k-1
     and snapshot k. It is timed by its distance from the fire edge at k-1, so the front sweeps
     outward from the old edge and reaches the edge of snapshot k exactly at its time. Patches that
     do not touch the old edge (spot fires, coverage gaps) start later in the interval.
  4. A light smoothing removes the grid's stair-steps.

Only perimeters dated inside the fire's own window are used (NIFC has a few with a wrong year).

Output (arrival.png, RGBA 8-bit): R,G = arrival time since t0 in 2-minute units (big-endian),
65535 = outside; B = 255 inside the burn; A = 255. manifest.json gets an "arrival" block.

  python export/arrival.py [slug ...]
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from scipy import ndimage
from shapely import wkb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'ingest'))
from common import connect  # noqa: E402

FIRES = HERE.parents[1] / 'ace.github.io' / 'projects' / 'wildfire-data' / 'app' / 'fires'
UNIT_S = 120                                        # one stored unit = 2 minutes
PIXEL_HALF_M = {'VIIRS': 190.0, 'MODIS': 500.0}     # half-width of a detection pixel at nadir
PASS_GAP_S = 20 * 60


def load(conn, eid, t_end):
    detections = conn.execute("""
        SELECT ST_X(ST_Transform(h.geom, 3857)), ST_Y(ST_Transform(h.geom, 3857)), extract(epoch FROM h.acq_time)::float8, s.instrument,
               coalesce(greatest(h.scan_km, h.track_km), 0)::float8
        FROM hotspot_event_link l JOIN hotspot h USING (hotspot_id, acq_time) JOIN sensor s ON s.sensor_id = h.sensor_id
        WHERE l.event_id = %(e)s AND l.score >= 0.6 AND coalesce(h.fire_type, 0) = 0
          AND (%(end)s::date IS NULL OR h.acq_time <= %(end)s::date + 5)
        ORDER BY h.acq_time""", dict(e=eid, end=t_end)).fetchall()
    perimeters = conn.execute("""
        SELECT extract(epoch FROM observed_at)::float8, ST_AsBinary(ST_Transform(geom, 3857)) FROM perimeter_obs
        WHERE event_id = %(e)s AND source = 'WFIGS' AND NOT is_final
          AND (%(end)s::date IS NULL OR observed_at <= %(end)s::date + 5)
          AND observed_at >= (SELECT discovered_at - interval '1 day' FROM fire_event WHERE event_id = %(e)s)
        ORDER BY observed_at""", dict(e=eid, end=t_end)).fetchall()
    final = conn.execute('SELECT ST_AsBinary(ST_Transform(final_perimeter, 3857)) FROM fire_event WHERE event_id = %s', (eid,)).fetchone()[0]
    return detections, perimeters, final


def pass_starts(times):
    """Index where each satellite pass starts, in a sorted list of detection times."""
    starts = [0]
    for i in range(1, len(times)):
        if times[i] - times[i - 1] > PASS_GAP_S:
            starts.append(i)
    return starts


def build(conn, slug, max_cells=900):
    m = json.loads((FIRES / slug / 'manifest.json').read_text())
    eid, b = m['event_id'], m['bounds_3857']
    w_m, h_m = b[2] - b[0], b[3] - b[1]
    cell = max(30.0, math.ceil(max(w_m, h_m) / max_cells / 10) * 10)          # Web Mercator metres
    nx, ny = int(math.ceil(w_m / cell)), int(math.ceil(h_m / cell))
    tf = from_origin(b[0], b[3], cell, cell)
    merc = 1 / math.cos(math.radians((m['bounds'][1] + m['bounds'][3]) / 2))   # Web Mercator metres per true metre

    detections, perimeters, final = load(conn, eid, m.get('contained'))
    if not detections:
        raise SystemExit(f'{slug}: no detections')
    t0 = detections[0][2] - 6 * 3600
    inside = ~geometry_mask([wkb.loads(bytes(final))], (ny, nx), tf) if final else np.ones((ny, nx), bool)
    perim_masks = [(tp - t0, ~geometry_mask([wkb.loads(bytes(g))], (ny, nx), tf)) for tp, g in perimeters]

    # ---- 1. snapshots: what is known to have burned at each observation time
    det_t = [d[2] - t0 for d in detections]
    starts = pass_starts(det_t)
    bounds = list(zip(starts, starts[1:] + [len(detections)]))
    events = sorted([(det_t[s], 'pass', (s, e)) for s, e in bounds] + [(tp, 'perim', pm) for tp, pm in perim_masks], key=lambda e: e[0])
    footprint = np.zeros((ny, nx), np.float32)
    yy, xx = np.mgrid[0:ny, 0:nx]
    sigma = max(1.0, 190.0 * merc / cell)
    snap_time, snap_k = [], np.full((ny, nx), -1, np.int32)
    known = np.zeros((ny, nx), bool)
    for t, kind, ref in events:
        if kind == 'pass':
            for x, y, _, inst, size_km in detections[ref[0]:ref[1]]:
                half_m = size_km * 500 if size_km else PIXEL_HALF_M.get(inst, 375.0)   # real pixel size: grows off-nadir
                r = half_m * merc / cell
                ci, cj = (x - b[0]) / cell, (b[3] - y) / cell
                i0, i1 = int(max(0, ci - r - 1)), int(min(nx, ci + r + 2))
                j0, j1 = int(max(0, cj - r - 1)), int(min(ny, cj + r + 2))
                if i0 >= i1 or j0 >= j1:
                    continue
                disc = ((xx[j0:j1, i0:i1] - ci) ** 2 + (yy[j0:j1, i0:i1] - cj) ** 2 <= r * r).astype(np.float32)
                np.maximum(footprint[j0:j1, i0:i1], disc, out=footprint[j0:j1, i0:i1])
            shape = ndimage.gaussian_filter(footprint, sigma) > 0.35       # neighbouring pixels merge into one smooth shape
            shape = ndimage.binary_fill_holes(shape | known)               # ground enclosed by fire was burning too
        else:
            shape = ref
        new = shape & inside & ~known
        if new.any():
            snap_k[new] = len(snap_time)
            snap_time.append(t)
            known |= new

    # ---- 2. gaps: nearest covered cell's snapshot
    gaps = inside & ~known
    if gaps.any() and known.any():
        _, (ji, ii) = ndimage.distance_transform_edt(~known, return_indices=True)
        snap_k[gaps] = snap_k[ji[gaps], ii[gaps]]
        known |= gaps
    mask = known

    # ---- 3. new ground in each snapshot, timed by distance from the previous fire edge
    arr = np.full((ny, nx), np.nan)
    burned = np.zeros((ny, nx), bool)
    for k, tk in enumerate(snap_time):
        new = snap_k == k
        if not new.any():
            continue
        t_prev = snap_time[k - 1] if k else max(0.0, tk - 6 * 3600)
        if burned.any():
            dist = ndimage.distance_transform_edt(~burned)
        else:                                                       # the very first snapshot grows from the first detection
            seed = np.ones((ny, nx), bool)
            x, y = detections[0][0], detections[0][1]
            seed[min(ny - 1, max(0, int((b[3] - y) / cell))), min(nx - 1, max(0, int((x - b[0]) / cell)))] = False
            dist = ndimage.distance_transform_edt(seed)
        labels, n = ndimage.label(new)
        for c in range(1, n + 1):
            cm = labels == c
            dv = dist[cm]
            lo, hi = dv.min(), dv.max()
            f = (dv - lo) / (hi - lo) if hi > lo else np.ones_like(dv)
            start = 0.0 if lo <= 1.5 else 0.35                       # detached patch: starts later in the interval
            arr[cm] = t_prev + (tk - t_prev) * (start + (1 - start) * f)
        burned |= new

    # ---- 4. smooth the grid's stair-steps
    filled = np.where(mask, arr, np.nanmax(arr[mask]))
    arr = np.where(mask, np.clip(ndimage.gaussian_filter(filled, 1.0), 0, None), np.nan)

    units = np.where(mask, np.clip(np.round(arr / UNIT_S), 0, 65534), 65535).astype(np.uint16)
    rgba = np.zeros((ny, nx, 4), np.uint8)
    rgba[..., 0], rgba[..., 1] = units >> 8, units & 255
    rgba[..., 2] = np.where(mask, 255, 0)
    rgba[..., 3] = 255
    Image.fromarray(rgba, 'RGBA').save(FIRES / slug / 'arrival.png', optimize=True)

    m['arrival'] = dict(t0=datetime.fromtimestamp(t0, timezone.utc).isoformat(), unit_s=UNIT_S, cell_m=cell / merc, nx=nx, ny=ny,
                        detections=len(detections), perimeters=len(perimeters), snapshots=len(snap_time),
                        cells=int(mask.sum()), span_h=float(np.nanmax(arr) / 3600))
    if 'arrival.png' not in m['files']:
        m['files'].append('arrival.png')
    (FIRES / slug / 'manifest.json').write_text(json.dumps(m, indent=1, default=str))
    print(f'{slug}: {nx}x{ny} cells of {cell / merc:.0f} m, {len(detections):,} detections, {len(perimeters)} perimeters, '
          f'{len(snap_time)} snapshots, span {m["arrival"]["span_h"]:.0f} h')


if __name__ == '__main__':
    with connect() as c:
        for s in sys.argv[1:] or sorted(p.name for p in FIRES.iterdir() if p.is_dir()):
            build(c, s)

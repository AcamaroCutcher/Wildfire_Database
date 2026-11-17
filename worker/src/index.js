/**
 * Fire Archive edge API (Cloudflare Worker).
 *
 *   GET /firms?source=VIIRS_NOAA21_NRT&bbox=-125,41,-116,47&days=1[&date=YYYY-MM-DD]
 *       NASA FIRMS active-fire detections as GeoJSON. FIRMS sends no CORS headers and needs a private
 *       MAP_KEY, so the browser can't call it directly; this route adds the key and CORS, and caches 10 min.
 *   GET /goes/latest?sat=19            newest GOES ABI Fire/Hot Spot (FDC, CONUS) file on NOAA's AWS bucket
 *   GET /fires?bbox=w,s,e,n&from=&to=&min_acres=   fire list from the Supabase serving copy (RPC fires_near)
 *   GET /event/:id                     one fire's full bundle (RPC event_bundle)
 *   GET /firesat                       placeholder until Earth Fire Alliance licences open (2027)
 *
 * Secrets (wrangler secret put …): FIRMS_MAP_KEY, SUPABASE_URL, SUPABASE_ANON_KEY
 * Vars (wrangler.toml): ALLOWED_ORIGINS
 */
const FIRMS = 'https://firms.modaps.eosdis.nasa.gov/api/area/csv';
const SOURCES = new Set(['VIIRS_NOAA20_NRT', 'VIIRS_NOAA21_NRT', 'VIIRS_NOAA20_SP', 'VIIRS_NOAA21_SP', 'VIIRS_SNPP_SP', 'MODIS_NRT', 'MODIS_SP', 'LANDSAT_NRT']);

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const origin = req.headers.get('Origin') || '';
    const allowed = (env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim());
    const cors = {
      'Access-Control-Allow-Origin': allowed.includes(origin) ? origin : allowed[0] || '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Max-Age': '86400',
      Vary: 'Origin',
    };
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (req.method !== 'GET') return json({ error: 'GET only' }, 405, cors);

    try {
      const cache = caches.default;
      const key = new Request(url.toString(), { method: 'GET' });
      const hit = await cache.match(key);
      if (hit) { const r = new Response(hit.body, hit); Object.entries(cors).forEach(([k, v]) => r.headers.set(k, v)); return r; }

      let res;
      if (url.pathname === '/firms') res = await firms(url, env);
      else if (url.pathname === '/goes/latest') res = await goesLatest(url);
      else if (url.pathname === '/fires') res = await supabaseRpc(env, 'fires_near', firesParams(url), 300);
      else if (url.pathname.startsWith('/event/')) res = await supabaseRpc(env, 'event_bundle', { p_event: +url.pathname.split('/')[2] }, 3600);
      else if (url.pathname === '/firesat') res = json({ status: 'not yet available', detail: 'FireSat data is licensed by Earth Fire Alliance: early adopters in 2026, non-commercial academic licences planned for 2027. https://earthfirealliance.org/data-access/' }, 501);
      else res = json({ routes: ['/firms', '/goes/latest', '/fires', '/event/:id', '/firesat'] }, 200);

      Object.entries(cors).forEach(([k, v]) => res.headers.set(k, v));
      if (res.ok && res.headers.get('Cache-Control')) ctx.waitUntil(cache.put(key, res.clone()));
      return res;
    } catch (e) {
      return json({ error: String(e.message || e) }, 502, cors);
    }
  },
};

function json(body, status = 200, headers = {}, maxAge = 0) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...(maxAge ? { 'Cache-Control': `public, max-age=${maxAge}` } : {}), ...headers } });
}

async function firms(url, env) {
  if (!env.FIRMS_MAP_KEY) return json({ error: 'FIRMS_MAP_KEY secret not set' }, 500);
  const source = url.searchParams.get('source') || 'VIIRS_NOAA21_NRT';
  if (!SOURCES.has(source)) return json({ error: `unknown source; use one of ${[...SOURCES].join(', ')}` }, 400);
  const bbox = (url.searchParams.get('bbox') || '-125,24,-66,50').split(',').map(Number);
  if (bbox.length !== 4 || bbox.some((v) => !Number.isFinite(v))) return json({ error: 'bbox must be west,south,east,north' }, 400);
  const days = Math.min(5, Math.max(1, +(url.searchParams.get('days') || 1)));
  const date = url.searchParams.get('date');
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) return json({ error: 'date must be YYYY-MM-DD' }, 400);
  const r = await fetch(`${FIRMS}/${env.FIRMS_MAP_KEY}/${source}/${bbox.join(',')}/${days}${date ? '/' + date : ''}`);
  const text = await r.text();
  if (!r.ok || !text.startsWith('latitude')) return json({ error: 'FIRMS: ' + text.slice(0, 200) }, 502);
  return json(csvToGeoJSON(text, source), 200, {}, 600);
}

function csvToGeoJSON(text, source) {
  const lines = text.trim().split('\n');
  const head = lines[0].split(',');
  const ix = (k) => head.indexOf(k);
  const lat = ix('latitude'), lon = ix('longitude'), d = ix('acq_date'), t = ix('acq_time');
  const features = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(',');
    const props = { source };
    head.forEach((h, j) => { if (j !== lat && j !== lon) props[h] = isNaN(+c[j]) || c[j] === '' ? c[j] : +c[j]; });
    const hhmm = String(c[t]).padStart(4, '0');
    props.time = `${c[d]}T${hhmm.slice(0, 2)}:${hhmm.slice(2)}:00Z`;
    features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [+c[lon], +c[lat]] }, properties: props });
  }
  return { type: 'FeatureCollection', features };
}

async function goesLatest(url) {
  const sat = url.searchParams.get('sat') === '18' ? '18' : '19';
  const now = new Date();
  for (let back = 0; back < 3; back++) {
    const t = new Date(now.getTime() - back * 3600e3);
    const doy = String(Math.floor((t - Date.UTC(t.getUTCFullYear(), 0, 0)) / 86400e3)).padStart(3, '0');
    const prefix = `ABI-L2-FDCC/${t.getUTCFullYear()}/${doy}/${String(t.getUTCHours()).padStart(2, '0')}/`;
    const r = await fetch(`https://noaa-goes${sat}.s3.amazonaws.com/?list-type=2&prefix=${prefix}`);
    const xml = await r.text();
    const keys = [...xml.matchAll(/<Key>([^<]+)<\/Key>/g)].map((m) => m[1]);
    if (keys.length) {
      const k = keys.sort().at(-1);
      return json({ satellite: `GOES-${sat}`, key: k, url: `https://noaa-goes${sat}.s3.amazonaws.com/${k}`,
        note: 'NetCDF4 (FDC: Mask, Power, Temp, Area). Convert with ingest/goes.py, or use the NASA GIBS GOES-East/West_ABI_FireTemp layers for a ready-made image.' }, 200, {}, 120);
    }
  }
  return json({ error: 'no recent file found' }, 404);
}

function firesParams(url) {
  const b = (url.searchParams.get('bbox') || '-125,24,-66,50').split(',').map(Number);
  return { p_west: b[0], p_south: b[1], p_east: b[2], p_north: b[3],
           p_from: url.searchParams.get('from') || '-infinity', p_to: url.searchParams.get('to') || 'infinity',
           p_min_acres: +(url.searchParams.get('min_acres') || 1000) };
}

async function supabaseRpc(env, fn, body, maxAge) {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return json({ error: 'Supabase secrets not set' }, 500);
  const r = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/${fn}`, {
    method: 'POST',
    headers: { apikey: env.SUPABASE_ANON_KEY, Authorization: `Bearer ${env.SUPABASE_ANON_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return new Response(await r.text(), { status: r.status, headers: { 'Content-Type': 'application/json', ...(r.ok ? { 'Cache-Control': `public, max-age=${maxAge}` } : {}) } });
}
